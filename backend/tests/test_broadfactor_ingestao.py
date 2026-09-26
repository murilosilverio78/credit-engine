import os
import sys
from datetime import date
from types import ModuleType, SimpleNamespace

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.integrations.broadfactor.client import (  # noqa: E402
    Cotacao,
    Documento,
    DocumentoAnexo,
    QuotationInactiveError,
)
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


class MemoryDatabase:
    def __init__(self, quotes=None):
        self.quotes = {row["cotacao_id"]: row.copy() for row in (quotes or [])}


def install_funnel(monkeypatch, cotacoes, documentos=None, initial_quotes=None):
    database = MemoryDatabase(initial_quotes)
    documentos = documentos or {}
    created = []
    analyses = []

    class FakeClient:
        def listar_cotacoes(self):
            return cotacoes

        def documentos_da_cotacao(self, cotacao_id):
            result = documentos.get(cotacao_id, [])
            if isinstance(result, Exception):
                raise result
            return result

    class FakeOperationService:
        async def create(self, **data):
            created.append(data)
            return {"id": f"operation-{data['cotacao_id']}"}

    fake_database = ModuleType("app.core.database")
    fake_database.supabase = database
    monkeypatch.setitem(sys.modules, "app.core.database", fake_database)

    fake_params = ModuleType("app.services.eligibility_params_service")
    fake_params.get_eligibility_config = lambda: PARAMS.copy()
    monkeypatch.setitem(sys.modules, "app.services.eligibility_params_service", fake_params)

    fake_operation = ModuleType("app.services.operation_service")
    fake_operation.OperationService = FakeOperationService
    monkeypatch.setitem(sys.modules, "app.services.operation_service", fake_operation)
    monkeypatch.setattr(broadfactor_ingestao, "BroadfactorClient", FakeClient)
    monkeypatch.setattr(broadfactor_ingestao, "_get_existing_operation", lambda *_: None)
    monkeypatch.setattr(broadfactor_ingestao, "_count_stages", lambda _: {})

    def persist(_, cotacao, valor_enquadrado):
        existing = database.quotes.get(cotacao.id, {})
        database.quotes[cotacao.id] = {
            **existing,
            "cotacao_id": cotacao.id,
            "valor_enquadrado": valor_enquadrado,
            "payload_bruto": cotacao.bruto,
            "ambiente": "PRODUCAO",
            "estagio": existing.get("estagio", "LISTA_ESPERA"),
            "estagio_max": existing.get("estagio_max", "LISTA_ESPERA"),
        }

    def load_state(_, cotacao_id):
        return database.quotes.get(cotacao_id, {}).copy()

    def update_stage(_, cotacao_id, estagio, **kwargs):
        row = database.quotes[cotacao_id]
        row.update({"estagio": estagio, "estagio_motivo": kwargs.get("motivo")})
        if estagio == "ENCERRADA":
            if kwargs.get("estagio_max") is not None:
                row["estagio_max"] = kwargs["estagio_max"]
        else:
            row["estagio_max"] = broadfactor_ingestao._stage_max(
                kwargs.get("estagio_max"), estagio
            )
        for field in ("operation_id", "n_documentos", "tipos_documento"):
            if kwargs.get(field) is not None:
                row[field] = kwargs[field]

    def close_missing(_, seen_ids):
        closed = 0
        for cotacao_id, row in database.quotes.items():
            if cotacao_id in seen_ids or row.get("estagio") == "ENCERRADA":
                continue
            maximum = broadfactor_ingestao._stage_max(
                row.get("estagio_max"), row.get("estagio") or "LISTA_ESPERA"
            )
            update_stage(
                database,
                cotacao_id,
                "ENCERRADA",
                motivo="cotacao_ausente_na_listagem",
                estagio_max=maximum,
            )
            closed += 1
        return closed

    async def start_analysis(operation_id):
        analyses.append(operation_id)
        return {"status": "aguardando_relatorio"}

    monkeypatch.setattr(broadfactor_ingestao, "_persist_quote", persist)
    monkeypatch.setattr(broadfactor_ingestao, "_load_quote_state", load_state)
    monkeypatch.setattr(broadfactor_ingestao, "_update_quote_stage", update_stage)
    monkeypatch.setattr(broadfactor_ingestao, "_mark_missing_quotes_closed", close_missing)
    monkeypatch.setattr(broadfactor_ingestao, "_update_quote_status", lambda *_args: None)
    monkeypatch.setattr(broadfactor_ingestao, "_start_analysis", start_analysis)
    return database, created, analyses


def document() -> DocumentoAnexo:
    return DocumentoAnexo(tipo="CONTRATO", arquivo="contrato.pdf")


@pytest.mark.asyncio
async def test_every_quote_is_persisted_and_only_eligible_quote_reaches_stage_two(monkeypatch):
    eligible = quote("C-eligible")
    rejected = quote("C-rejected", valor=5_000)
    database, created, analyses = install_funnel(monkeypatch, [eligible, rejected])

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert set(database.quotes) == {"C-eligible", "C-rejected"}
    assert database.quotes["C-eligible"]["estagio"] == "ENQUADRADA"
    assert database.quotes["C-eligible"]["estagio_motivo"] == "sem_documentos_broadfactor"
    assert database.quotes["C-rejected"]["estagio"] == "LISTA_ESPERA"
    assert database.quotes["C-rejected"]["estagio_motivo"] == "abaixo_ticket_minimo"
    assert result["aprovadas"] == 1
    assert result["descartadas"] == 1
    assert created == []
    assert analyses == []


@pytest.mark.asyncio
async def test_stage_two_stays_enquadrada_without_document(monkeypatch):
    database, created, analyses = install_funnel(
        monkeypatch,
        [quote("C-no-doc")],
        initial_quotes=[{
            "cotacao_id": "C-no-doc",
            "ambiente": "PRODUCAO",
            "estagio": "ENQUADRADA",
            "estagio_max": "ENQUADRADA",
        }],
    )

    await broadfactor_ingestao.run_broadfactor_ingestao()

    row = database.quotes["C-no-doc"]
    assert row["estagio"] == "ENQUADRADA"
    assert row["n_documentos"] == 0
    assert row["estagio_motivo"] == "sem_documentos_broadfactor"
    assert created == []
    assert analyses == []


@pytest.mark.asyncio
async def test_stage_two_with_document_reaches_three_and_runs_only_partial_analysis(monkeypatch):
    database, created, analyses = install_funnel(
        monkeypatch,
        [quote("C-doc")],
        documentos={"C-doc": [document()]},
        initial_quotes=[{
            "cotacao_id": "C-doc",
            "ambiente": "PRODUCAO",
            "estagio": "ENQUADRADA",
            "estagio_max": "ENQUADRADA",
        }],
    )

    await broadfactor_ingestao.run_broadfactor_ingestao()

    row = database.quotes["C-doc"]
    assert row["estagio"] == "DOCUMENTADA"
    assert row["n_documentos"] == 1
    assert row["tipos_documento"] == ["CONTRATO"]
    assert len(created) == 1
    assert analyses == ["operation-C-doc"]


@pytest.mark.asyncio
async def test_missing_quote_is_closed_preserving_maximum_stage(monkeypatch):
    database, _, _ = install_funnel(
        monkeypatch,
        [],
        initial_quotes=[{
            "cotacao_id": "C-gone",
            "ambiente": "PRODUCAO",
            "estagio": "DOCUMENTADA",
            "estagio_max": "QUALIFICADA",
        }],
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert database.quotes["C-gone"]["estagio"] == "ENCERRADA"
    assert database.quotes["C-gone"]["estagio_max"] == "QUALIFICADA"
    assert result["encerradas"] == 1


@pytest.mark.asyncio
async def test_inactive_listed_quote_is_closed_preserving_maximum_stage(monkeypatch):
    database, created, analyses = install_funnel(
        monkeypatch,
        [quote("C-inactive")],
        documentos={
            "C-inactive": QuotationInactiveError("QUOTATION_INACTIVE")
        },
        initial_quotes=[{
            "cotacao_id": "C-inactive",
            "ambiente": "PRODUCAO",
            "estagio": "DOCUMENTADA",
            "estagio_max": "QUALIFICADA",
            "operation_id": "operation-C-inactive",
        }],
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert database.quotes["C-inactive"]["estagio"] == "ENCERRADA"
    assert database.quotes["C-inactive"]["estagio_max"] == "QUALIFICADA"
    assert database.quotes["C-inactive"]["estagio_motivo"] == "QUOTATION_INACTIVE"
    assert result["encerradas"] == 1
    assert created == []
    assert analyses == []


@pytest.mark.asyncio
async def test_reingestion_upserts_quote_and_does_not_create_duplicate_operation(monkeypatch):
    database, created, analyses = install_funnel(
        monkeypatch,
        [quote("C-repeat")],
        documentos={"C-repeat": [document()]},
        initial_quotes=[{
            "cotacao_id": "C-repeat",
            "ambiente": "PRODUCAO",
            "estagio": "DOCUMENTADA",
            "estagio_max": "DOCUMENTADA",
            "operation_id": "operation-C-repeat",
        }],
    )
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_get_existing_operation",
        lambda *_: {"id": "operation-C-repeat", "status": "aguardando_relatorio"},
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert list(database.quotes) == ["C-repeat"]
    assert created == []
    assert analyses == ["operation-C-repeat"]
    assert result["duplicadas"] == 1


@pytest.mark.asyncio
async def test_partial_analysis_contract_never_dispatches_paid_components(monkeypatch):
    calls = []

    async def start_analysis(operation_id, *, ate_fase=None):
        calls.append((operation_id, ate_fase))

    from app.workers.tasks import orchestrator

    monkeypatch.setattr(orchestrator, "start_analysis", start_analysis)
    await broadfactor_ingestao._start_analysis("op-1")

    assert calls == [("op-1", 2)]
    assert "contrato_extracao" not in orchestrator.PHASE2_FUNIL_COMPONENTS
    assert "web_research" not in orchestrator.PHASE2_FUNIL_COMPONENTS
    assert "score_engine" not in orchestrator.PHASE2_FUNIL_COMPONENTS


def test_persist_quote_keeps_raw_payload():
    saved = {}

    class Query:
        def upsert(self, data, on_conflict):
            saved.update(data)
            saved["on_conflict"] = on_conflict
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    class Supabase:
        def table(self, name):
            assert name == "cotacoes_broadfactor"
            return Query()

    broadfactor_ingestao._persist_quote(Supabase(), quote("C-raw"), 300_000)

    assert saved["payload_bruto"] == {"id": "C-raw", "campoInstavel": "preservado"}
    assert saved["ambiente"] == "PRODUCAO"
    assert saved["on_conflict"] == "cotacao_id"


def test_close_update_persists_explicit_maximum_stage(monkeypatch):
    saved = {}

    class Query:
        def update(self, data):
            saved.update(data)
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    class Supabase:
        def table(self, _name):
            return Query()

    monkeypatch.setattr(
        broadfactor_ingestao,
        "_execute_with_retry",
        lambda _id, _component, _action, request: request(),
    )
    broadfactor_ingestao._update_quote_stage(
        Supabase(), "C-1", "ENCERRADA", estagio_max="QUALIFICADA"
    )

    assert saved["estagio"] == "ENCERRADA"
    assert saved["estagio_max"] == "QUALIFICADA"


@pytest.mark.asyncio
async def test_one_quote_failure_does_not_block_following_quotes(monkeypatch):
    first = quote("C-fails")
    second = quote("C-works")
    database, created, analyses = install_funnel(
        monkeypatch,
        [first, second],
        documentos={first.id: [document()], second.id: [document()]},
    )
    persist = broadfactor_ingestao._persist_quote

    def fail_first(supabase, cotacao, valor_enquadrado):
        if cotacao.id == first.id:
            raise ConnectionError("temporary failure")
        persist(supabase, cotacao, valor_enquadrado)

    monkeypatch.setattr(broadfactor_ingestao, "_persist_quote", fail_first)

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert set(database.quotes) == {second.id}
    assert [item["cotacao_id"] for item in created] == [second.id]
    assert analyses == ["operation-C-works"]
    assert result["criadas"] == 1
    assert result["falhas"] == 1


@pytest.mark.asyncio
async def test_failed_operation_is_reprocessed_without_creating_another(monkeypatch):
    database, created, analyses = install_funnel(
        monkeypatch,
        [quote("C-retry")],
        initial_quotes=[{
            "cotacao_id": "C-retry",
            "ambiente": "PRODUCAO",
            "estagio": "DOCUMENTADA",
            "estagio_max": "DOCUMENTADA",
            "operation_id": "operation-C-retry",
        }],
    )
    existing = {
        "id": "operation-C-retry",
        "status": "failed",
        "analysis_attempts": 1,
    }
    statuses = []
    monkeypatch.setattr(broadfactor_ingestao, "_get_existing_operation", lambda *_: existing)
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_claim_failed_operation",
        lambda *_: {**existing, "status": "processing", "analysis_attempts": 2},
    )
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_update_quote_status",
        lambda _, cotacao_id, status, operation_id=None: statuses.append(
            (cotacao_id, status, operation_id)
        ),
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert database.quotes["C-retry"]["estagio"] == "DOCUMENTADA"
    assert created == []
    assert analyses == ["operation-C-retry"]
    assert statuses == [
        ("C-retry", "REPROCESSANDO", "operation-C-retry"),
        ("C-retry", "ANALISE_CONCLUIDA", "operation-C-retry"),
    ]
    assert result["reprocessadas"] == 1
    assert result["criadas"] == 0


@pytest.mark.asyncio
async def test_failed_operation_stops_after_maximum_attempts(monkeypatch):
    _, created, analyses = install_funnel(
        monkeypatch,
        [quote("C-exhausted")],
        initial_quotes=[{
            "cotacao_id": "C-exhausted",
            "ambiente": "PRODUCAO",
            "estagio": "DOCUMENTADA",
            "estagio_max": "DOCUMENTADA",
            "operation_id": "operation-C-exhausted",
        }],
    )
    statuses = []
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_get_existing_operation",
        lambda *_: {
            "id": "operation-C-exhausted",
            "status": "failed",
            "analysis_attempts": broadfactor_ingestao.MAX_ANALYSIS_ATTEMPTS,
        },
    )
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_claim_failed_operation",
        lambda *_: pytest.fail("exhausted operation was claimed"),
    )
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_update_quote_status",
        lambda _, cotacao_id, status, operation_id=None: statuses.append(
            (cotacao_id, status, operation_id)
        ),
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert created == []
    assert analyses == []
    assert statuses == [
        ("C-exhausted", "ERRO_ANALISE_FINAL", "operation-C-exhausted")
    ]
    assert result["reprocessadas"] == 0
    assert result["tentativas_esgotadas"] == 1


@pytest.mark.asyncio
async def test_quote_status_or_stage_failure_does_not_block_partial_analysis(monkeypatch):
    _, created, analyses = install_funnel(
        monkeypatch,
        [quote("C-status-fails")],
        documentos={"C-status-fails": [document()]},
    )

    def fail_status(*_args):
        raise ConnectionError("status unavailable")

    monkeypatch.setattr(broadfactor_ingestao, "_update_quote_status", fail_status)

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert [item["cotacao_id"] for item in created] == ["C-status-fails"]
    assert analyses == ["operation-C-status-fails"]
    assert result["criadas"] == 1
    assert result["falhas"] >= 1

    monkeypatch.setattr(
        broadfactor_ingestao,
        "_execute_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ConnectionError("stage unavailable")
        ),
    )
    broadfactor_ingestao._update_quote_stage(
        object(),
        "C-status-fails",
        "DOCUMENTADA",
        estagio_max="ENQUADRADA",
    )


@pytest.mark.asyncio
async def test_analysis_error_is_persisted_on_quote(monkeypatch):
    _, created, _ = install_funnel(
        monkeypatch,
        [quote("C-analysis-fails")],
        documentos={"C-analysis-fails": [document()]},
    )
    statuses = []

    async def fail_analysis(_operation_id):
        raise RuntimeError("phase2_validation: Server disconnected")

    monkeypatch.setattr(broadfactor_ingestao, "_start_analysis", fail_analysis)
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_update_quote_status",
        lambda _, cotacao_id, status, operation_id=None: statuses.append(
            (cotacao_id, status, operation_id)
        ),
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao()

    assert [item["cotacao_id"] for item in created] == ["C-analysis-fails"]
    assert statuses == [
        ("C-analysis-fails", "OPERACAO_CRIADA", "operation-C-analysis-fails"),
        ("C-analysis-fails", "ERRO_ANALISE", "operation-C-analysis-fails"),
    ]
    assert result["falhas"] == 1


@pytest.mark.asyncio
async def test_limit_counts_created_operations_not_duplicates(monkeypatch):
    existing = quote("C-existing")
    first_new = quote("C-first-new")
    second_new = quote("C-second-new")
    database, created, analyses = install_funnel(
        monkeypatch,
        [existing, first_new, second_new],
        initial_quotes=[
            {
                "cotacao_id": item.id,
                "ambiente": "PRODUCAO",
                "estagio": "DOCUMENTADA",
                "estagio_max": "DOCUMENTADA",
                **(
                    {"operation_id": "operation-C-existing"}
                    if item.id == existing.id
                    else {}
                ),
            }
            for item in (existing, first_new, second_new)
        ],
    )
    monkeypatch.setattr(
        broadfactor_ingestao,
        "_get_existing_operation",
        lambda _, cotacao_id: (
            {"id": "operation-C-existing", "status": "aguardando_relatorio"}
            if cotacao_id == existing.id
            else None
        ),
    )

    result = await broadfactor_ingestao.run_broadfactor_ingestao(limit=1)

    assert set(database.quotes) == {existing.id, first_new.id, second_new.id}
    assert [item["cotacao_id"] for item in created] == [first_new.id]
    assert analyses == ["operation-C-existing", "operation-C-first-new"]
    assert result["duplicadas"] == 1
    assert result["criadas"] == 1


def test_failed_operation_claim_is_conditional_and_increments_attempts(monkeypatch):
    captured = {"filters": []}

    class Query:
        def update(self, data):
            captured["data"] = data
            return self

        def eq(self, field, value):
            captured["filters"].append((field, value))
            return self

        def execute(self):
            return SimpleNamespace(data=[{"id": "op-1", "status": "processing"}])

    class Supabase:
        def table(self, name):
            assert name == "operations"
            return Query()

    monkeypatch.setattr(
        broadfactor_ingestao,
        "_execute_with_retry",
        lambda _operation_id, _component, _action, request: request(),
    )

    claimed = broadfactor_ingestao._claim_failed_operation(
        Supabase(),
        {"id": "op-1", "status": "failed", "analysis_attempts": 1},
    )

    assert claimed["analysis_attempts"] == 2
    assert captured["data"] == {
        "status": "processing",
        "analysis_attempts": 2,
        "error_message": None,
        "completed_at": None,
    }
    assert ("status", "failed") in captured["filters"]
    assert ("analysis_attempts", 1) in captured["filters"]
