import asyncio
import os
from types import SimpleNamespace


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.workers.tasks import orchestrator, score_engine  # noqa: E402


PENALTY_PARAMS = {
    "penalidade_cnd_federal_ausente": 6,
    "penalidade_cndt_ausente": 6,
    "penalidade_fgts_ausente": 6,
}


class Query:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.action = None
        self.payload = None
        self.filters = []

    def select(self, *_args, **_kwargs):
        self.action = "select"
        return self

    def insert(self, payload):
        self.action = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.action = "update"
        self.payload = payload
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def in_(self, field, value):
        self.filters.append(("in", field, value))
        return self

    def execute(self):
        self.database.queries.append(self)
        if self.action == "select":
            return SimpleNamespace(data=self.database.select_data.get(self.table, []))
        if self.table == "operations" and self.database.operation_update_succeeds:
            return SimpleNamespace(data=[{"id": "op-1"}])
        return SimpleNamespace(data=[])


class FakeSupabase:
    def __init__(self, select_data=None, operation_update_succeeds=True):
        self.select_data = select_data or {}
        self.operation_update_succeeds = operation_update_succeeds
        self.queries = []

    def table(self, name):
        return Query(self, name)


def _pricing_config():
    return PENALTY_PARAMS, {"A": {"pd_mult": 0.6}, "B": {"pd_mult": 1.0}}


def _valid_certificates(*components):
    return {
        component: {"resultado": "negativa", "valida": True}
        for component in components
    }


def _fixed_dimension(name):
    return score_engine._dimension(
        88,
        score_engine.PESOS_MERITO[name],
        [],
        "Dimensao fixa para teste.",
        fonte="test",
    )


def _consolidate_88(monkeypatch, snapshots):
    monkeypatch.setattr(
        "app.services.pricing_params_service.get_pricing_config",
        _pricing_config,
    )
    monkeypatch.setattr(score_engine, "gates_deterministicos", lambda _snapshots: [])
    monkeypatch.setattr(
        score_engine,
        "score_relacionamento",
        lambda _snapshots: _fixed_dimension("relacionamento_governamental"),
    )
    monkeypatch.setattr(
        score_engine,
        "score_saude_cadastral",
        lambda _snapshots: _fixed_dimension("saude_cadastral"),
    )
    monkeypatch.setattr(
        score_engine,
        "score_reputacao",
        lambda _snapshots: _fixed_dimension("reputacao_mercado"),
    )
    monkeypatch.setattr(
        score_engine,
        "_ajuste_pd_volatilidade",
        lambda _snapshots, _rating: ({}, []),
    )

    return score_engine.consolidar_score(
        "31822605000191",
        snapshots,
        porte_dimension=_fixed_dimension("porte_operacionalidade"),
    )


def test_after_phase2_keeps_waiting_upload_and_continues_pipeline(monkeypatch):
    database = FakeSupabase({
        "component_config": [
            {"component": component} for component in orchestrator.MANUAL_COMPONENTS
        ],
        "upload_tasks": [],
    })
    phase_calls = []

    async def fake_phase3_4(operation_id):
        phase_calls.append(operation_id)
        return {"operation_id": operation_id, "status": "completed"}

    monkeypatch.setattr(orchestrator, "supabase", database)
    monkeypatch.setattr(orchestrator, "_phase3_4", fake_phase3_4)

    result = asyncio.run(orchestrator._after_phase2("op-1"))

    assert result["status"] == "completed"
    assert phase_calls == ["op-1"]
    assert any(
        query.table == "component_snapshots"
        and query.action == "update"
        and query.payload == {"status": "waiting_upload"}
        for query in database.queries
    )
    assert not any(query.table == "operations" for query in database.queries)


def test_score_preconditions_ignore_waiting_manual_certificates():
    snapshots = [
        {"component": component, "status": "completed", "started_at": None}
        for component in score_engine.ESSENTIAL_COMPONENTS
    ] + [
        {"component": component, "status": "waiting_upload", "started_at": None}
        for component in score_engine.CERTIDOES_REGULARIDADE
    ]
    database = FakeSupabase({"component_snapshots": snapshots})

    score_engine.validate_score_preconditions("op-1", database)


def test_missing_certificates_apply_individual_flags_and_penalties(monkeypatch):
    monkeypatch.setattr(
        "app.services.pricing_params_service.get_pricing_config",
        _pricing_config,
    )

    regularidade = score_engine.score_regularidade({})

    assert regularidade["fator_potencial"] == 1.0
    assert regularidade["fator"] == 0.82
    assert regularidade["penalizacao_total"] == 18
    assert regularidade["pendencias_rating"] == [
        {"certidao": "cnd_federal", "penalizacao": 6},
        {"certidao": "cndt_tst", "penalizacao": 6},
        {"certidao": "fgts", "penalizacao": 6},
    ]
    assert "certidao_cnd_federal_pendente" in regularidade["flags"]
    assert "certidao_cndt_pendente" in regularidade["flags"]
    assert "certidao_fgts_pendente" in regularidade["flags"]


def test_rating_potential_and_effective_rating_reflect_missing_certificates(monkeypatch):
    all_missing = _consolidate_88(monkeypatch, {})
    one_missing = _consolidate_88(
        monkeypatch,
        _valid_certificates("cndt_tst", "fgts"),
    )
    none_missing = _consolidate_88(
        monkeypatch,
        _valid_certificates(*score_engine.CERTIDOES_REGULARIDADE),
    )

    assert (all_missing["score"], all_missing["rating"]) == (72.2, "B")
    assert all_missing["rating_potencial"] == "A"
    assert all_missing["penalizacao_total"] == 18
    assert (one_missing["score"], one_missing["rating"]) == (82.7, "B")
    assert one_missing["rating_potencial"] == "A"
    assert one_missing["penalizacao_total"] == 6
    assert none_missing["score"] == 88
    assert none_missing["rating"] == none_missing["rating_potencial"] == "A"
    assert none_missing["penalizacao_total"] == 0


def test_completed_operation_can_be_reprocessed_after_upload(monkeypatch):
    database = FakeSupabase()
    phase_calls = []

    async def fake_phase3_4(operation_id):
        phase_calls.append(operation_id)
        return {"operation_id": operation_id, "status": "completed"}

    monkeypatch.setattr(orchestrator, "supabase", database)
    monkeypatch.setattr(orchestrator, "_phase3_4", fake_phase3_4)

    result = asyncio.run(orchestrator.resume_after_upload("op-1"))

    operation_update = next(
        query
        for query in database.queries
        if query.table == "operations" and query.action == "update"
    )
    assert ("in", "status", ["manual_review", "completed"]) in operation_update.filters
    assert operation_update.payload["status"] == "processing"
    assert operation_update.payload["completed_at"] is None
    assert phase_calls == ["op-1"]
    assert result["status"] == "completed"
