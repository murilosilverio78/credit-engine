import os
import sys
from datetime import date
from types import ModuleType

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.integrations.broadfactor.client import Cotacao, Documento  # noqa: E402
from app.workers.tasks import broadfactor_ingestao  # noqa: E402


PARAMS = {
    "ticket_minimo": 10_000,
    "ticket_maximo": 5_000_000,
    "pct_max_contrato": 0.50,
    "prazo_padrao_meses": 12,
    "dias_minimos_expiracao": 5,
}


def quote(cotacao_id: str, valor: float = 300_000) -> Cotacao:
    return Cotacao(
        id=cotacao_id,
        nome_fornecedor=f"Fornecedor {cotacao_id}",
        documento=Documento.de("03.012.610/0001-01"),
        valor=valor,
        data=date(2026, 9, 3),
        data_expiracao=date(2026, 12, 31),
        margem_disponivel=560_000,
        tipo="CONTRATO",
        bruto={"id": cotacao_id, "campoInstavel": "preservado"},
    )


def install_service_modules(monkeypatch, *, discarded, created):
    fake_database = ModuleType("app.core.database")
    fake_database.supabase = object()
    monkeypatch.setitem(sys.modules, "app.core.database", fake_database)

    fake_params = ModuleType("app.services.eligibility_params_service")
    fake_params.get_eligibility_config = lambda: PARAMS.copy()
    monkeypatch.setitem(
        sys.modules,
        "app.services.eligibility_params_service",
        fake_params,
    )

    fake_discard = ModuleType("app.services.ingestion_discard_service")
    fake_discard.record_ingestion_discard = lambda **data: discarded.append(data)
    monkeypatch.setitem(
        sys.modules,
        "app.services.ingestion_discard_service",
        fake_discard,
    )

    class FakeOperationService:
        async def create(self, **data):
            created.append(data)
            return {"id": f"operation-{data['cotacao_id']}"}

    fake_operation = ModuleType("app.services.operation_service")
    fake_operation.OperationService = FakeOperationService
    monkeypatch.setitem(sys.modules, "app.services.operation_service", fake_operation)


def test_persist_quote_keeps_raw_payload():
    saved = {}

    class Query:
        def upsert(self, data, on_conflict):
            saved.update(data)
            saved["on_conflict"] = on_conflict
            return self

        def execute(self):
            return None

    class Supabase:
        def table(self, name):
            assert name == "cotacoes_broadfactor"
            return Query()

    broadfactor_ingestao._persist_quote(Supabase(), quote("C-raw"), 300_000)

    assert saved["payload_bruto"] == {
        "id": "C-raw",
        "campoInstavel": "preservado",
    }
    assert saved["saldo_vincendo"] == 800_000
    assert saved["valor_enquadrado"] == 300_000
    assert saved["on_conflict"] == "cotacao_id"


@pytest.mark.asyncio
async def test_ingestion_persists_and_starts_analysis(monkeypatch):
    approved = quote("C-1")
    rejected = quote("C-2", valor=5_000)
    discarded = []
    created = []
    persisted = []
    statuses = []
    analyses = []

    class FakeClient:
        def triar(self, **params):
            assert params == {
                "ticket_minimo": 10_000,
                "ticket_maximo": 5_000_000,
                "pct_max_contrato": 0.50,
                "dias_minimos_expiracao": 5,
            }
            return [approved], [(rejected, "abaixo_ticket_minimo")]

    install_service_modules(
        monkeypatch,
        discarded=discarded,
        created=created,
    )
    monkeypatch.setattr(broadfactor_ingestao, "BroadfactorClient", FakeClient)
    monkeypatch.setattr(broadfactor_ingestao, "_operation_exists", lambda *_: False)
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_persist_quote",
        lambda _, cotacao, valor: persisted.append((cotacao, valor)),
    )
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_update_quote_status",
        lambda _, cotacao_id, status, operation_id=None: statuses.append(
            (cotacao_id, status, operation_id)
        ),
    )

    async def fake_start_analysis(operation_id):
        analyses.append(operation_id)

    monkeypatch.setattr(
        broadfactor_ingestao,
        "_start_analysis",
        fake_start_analysis,
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert persisted == [(approved, 300_000)]
    assert created[0] == {
        "cnpj": "03012610000101",
        "origem_dados": "API_BROADFACTOR",
        "cotacao_id": "C-1",
        "valor_solicitado": 300_000,
        "valor_enquadrado": 300_000,
        "saldo_vincendo": 800_000,
        "margem_disponivel": 560_000,
        "prazo_final_meses": 12,
        "prazo_vincendo_indisponivel": True,
        "source": "broadfactor_ingestao",
    }
    assert statuses == [("C-1", "OPERACAO_CRIADA", "operation-C-1")]
    assert analyses == ["operation-C-1"]
    assert discarded[0]["estagio"] == "S0_INGESTAO"
    assert discarded[0]["motivo"] == "abaixo_ticket_minimo"
    assert result == {
        "status": "completed",
        "total": 2,
        "aprovadas": 1,
        "descartadas": 1,
        "descartadas_por_motivo": {"abaixo_ticket_minimo": 1},
        "criadas": 1,
        "duplicadas": 0,
        "falhas": 0,
    }


@pytest.mark.asyncio
async def test_one_quote_failure_does_not_block_the_next(monkeypatch):
    first = quote("C-fails")
    second = quote("C-works")
    discarded = []
    created = []
    persisted_ids = []
    analyzed = []

    class FakeClient:
        def triar(self, **_):
            return [first, second], []

    install_service_modules(
        monkeypatch,
        discarded=discarded,
        created=created,
    )
    monkeypatch.setattr(broadfactor_ingestao, "BroadfactorClient", FakeClient)
    monkeypatch.setattr(broadfactor_ingestao, "_operation_exists", lambda *_: False)

    def persist(_, cotacao, __):
        if cotacao.id == "C-fails":
            raise ConnectionError("temporary failure")
        persisted_ids.append(cotacao.id)

    monkeypatch.setattr(broadfactor_ingestao, "_persist_quote", persist)
    monkeypatch.setattr(broadfactor_ingestao, "_update_quote_status", lambda *args: None)

    async def fake_start_analysis(operation_id):
        analyzed.append(operation_id)

    monkeypatch.setattr(
        broadfactor_ingestao,
        "_start_analysis",
        fake_start_analysis,
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert persisted_ids == ["C-works"]
    assert [item["cotacao_id"] for item in created] == ["C-works"]
    assert analyzed == ["operation-C-works"]
    assert result["criadas"] == 1
    assert result["falhas"] == 1


@pytest.mark.asyncio
async def test_existing_operation_is_skipped(monkeypatch):
    existing = quote("C-existing")
    discarded = []
    created = []

    class FakeClient:
        def triar(self, **_):
            return [existing], []

    install_service_modules(
        monkeypatch,
        discarded=discarded,
        created=created,
    )
    monkeypatch.setattr(broadfactor_ingestao, "BroadfactorClient", FakeClient)
    monkeypatch.setattr(broadfactor_ingestao, "_operation_exists", lambda *_: True)

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert created == []
    assert result["duplicadas"] == 1
    assert result["criadas"] == 0


@pytest.mark.asyncio
async def test_status_update_failure_does_not_block_analysis(monkeypatch):
    approved = quote("C-status-fails")
    discarded = []
    created = []
    analyzed = []

    class FakeClient:
        def triar(self, **_):
            return [approved], []

    install_service_modules(
        monkeypatch,
        discarded=discarded,
        created=created,
    )
    monkeypatch.setattr(broadfactor_ingestao, "BroadfactorClient", FakeClient)
    monkeypatch.setattr(broadfactor_ingestao, "_operation_exists", lambda *_: False)
    monkeypatch.setattr(broadfactor_ingestao, "_persist_quote", lambda *args: None)

    def fail_status(*args):
        raise ConnectionError("status unavailable")

    monkeypatch.setattr(broadfactor_ingestao, "_update_quote_status", fail_status)

    async def fake_start_analysis(operation_id):
        analyzed.append(operation_id)

    monkeypatch.setattr(
        broadfactor_ingestao,
        "_start_analysis",
        fake_start_analysis,
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert analyzed == ["operation-C-status-fails"]
    assert result["criadas"] == 1
    assert result["falhas"] == 1


@pytest.mark.asyncio
async def test_dry_run_only_returns_triage_summary(monkeypatch):
    approved = quote("C-dry")
    rejected = quote("C-dry-rejected", valor=5_000)
    discarded = []
    created = []

    class FakeClient:
        def triar(self, **_):
            return [approved], [(rejected, "abaixo_ticket_minimo")]

    install_service_modules(
        monkeypatch,
        discarded=discarded,
        created=created,
    )
    monkeypatch.setattr(broadfactor_ingestao, "BroadfactorClient", FakeClient)
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_persist_quote",
        lambda *args: pytest.fail("dry-run attempted to persist a quote"),
    )
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_start_analysis",
        lambda *args: pytest.fail("dry-run attempted to start analysis"),
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao(dry_run=True)

    assert created == []
    assert discarded == []
    assert result == {
        "status": "dry_run",
        "total": 2,
        "aprovadas": 1,
        "descartadas": 1,
        "descartadas_por_motivo": {"abaixo_ticket_minimo": 1},
        "criadas": 0,
        "duplicadas": 0,
        "falhas": 0,
    }


@pytest.mark.asyncio
async def test_limit_does_not_count_duplicates(monkeypatch):
    existing = quote("C-existing")
    first_new = quote("C-first-new")
    second_new = quote("C-second-new")
    discarded = []
    created = []
    persisted = []

    class FakeClient:
        def triar(self, **_):
            return [existing, first_new, second_new], []

    install_service_modules(
        monkeypatch,
        discarded=discarded,
        created=created,
    )
    monkeypatch.setattr(broadfactor_ingestao, "BroadfactorClient", FakeClient)
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_operation_exists",
        lambda _, cotacao_id: cotacao_id == "C-existing",
    )
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_persist_quote",
        lambda _, cotacao, __: persisted.append(cotacao.id),
    )
    monkeypatch.setattr(broadfactor_ingestao, "_update_quote_status", lambda *args: None)

    async def fake_start_analysis(operation_id):
        return {"operation_id": operation_id}

    monkeypatch.setattr(
        broadfactor_ingestao,
        "_start_analysis",
        fake_start_analysis,
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao(limit=1)

    assert persisted == ["C-first-new"]
    assert [item["cotacao_id"] for item in created] == ["C-first-new"]
    assert result["duplicadas"] == 1
    assert result["criadas"] == 1
