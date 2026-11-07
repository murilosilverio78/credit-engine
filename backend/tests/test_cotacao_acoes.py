import asyncio

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from app.api.v1.endpoints import cotacoes
from app.services import audit_service
from tests.fakes.postgrest import Postgrest


def _request():
    return Request({"type": "http", "method": "POST", "headers": [], "client": ("127.0.0.1", 1234)})


def _quote(motivo="acima_ticket_maximo", **extra):
    return {"cotacao_id": "C-acao", "cnpj": "00000000000100", "estagio": "LISTA_ESPERA", "estagio_max": "LISTA_ESPERA", "estagio_motivo": motivo, "valor_enquadrado": 700000, "valor_solicitado": 950000, "saldo_vincendo": 2_000_000, "n_documentos": 0, **extra}


def _db(quote, operations=None, snapshots=None):
    database = Postgrest({"cotacoes_broadfactor": [quote], "operations": operations or [], "component_snapshots": snapshots or [], "audit_trail": []})
    return database


def _patch(monkeypatch, database):
    monkeypatch.setattr(cotacoes, "supabase", database)
    monkeypatch.setattr(audit_service, "supabase", database)
    monkeypatch.setattr(cotacoes, "_params", lambda: {"ticket_maximo": 700000, "ticket_minimo": 50000, "pct_max_contrato": 0.5})


def test_arquivar_preserves_maximum_audits_and_is_idempotent(monkeypatch):
    database = _db(_quote("sem_documentos_broadfactor", estagio="ENQUADRADA", estagio_max="DOCUMENTADA"))
    _patch(monkeypatch, database)
    user = {"id": "u-1", "role": "gerente"}
    result = asyncio.run(cotacoes.arquivar_cotacao("C-acao", cotacoes.JustificativaInput(justificativa="sem aderência"), _request(), user))
    quote = database.tables["cotacoes_broadfactor"][0]
    assert result["status"] == "archived" and quote["estagio_max"] == "DOCUMENTADA"
    assert database.tables["audit_trail"][0]["action"] == "quote_archived"
    assert asyncio.run(cotacoes.arquivar_cotacao("C-acao", cotacoes.JustificativaInput(), _request(), user))["idempotent"] is True


def test_analise_completa_blocks_veto_and_requires_reason(monkeypatch):
    operation = {"id": "op-1", "status": "reprovada_triagem"}
    database = _db(_quote("capacidade_insuficiente:0", estagio="DOCUMENTADA", operation_id="op-1"), [operation])
    _patch(monkeypatch, database)
    user = {"id": "u-1", "role": "diretor"}
    with pytest.raises(HTTPException, match="Justificativa"):
        asyncio.run(cotacoes.solicitar_analise_completa("C-acao", cotacoes.JustificativaInput(), BackgroundTasks(), _request(), user))
    result = asyncio.run(cotacoes.solicitar_analise_completa("C-acao", cotacoes.JustificativaInput(justificativa="Justificativa válida"), BackgroundTasks(), _request(), user))
    assert result["status"] == "accepted"
    assert database.tables["audit_trail"][0]["action"] == "quote_full_analysis_requested"
    database.tables["cotacoes_broadfactor"][0]["estagio_motivo"] = "Sancao ativa em CEIS"
    with pytest.raises(HTTPException, match="veto"):
        asyncio.run(cotacoes.solicitar_analise_completa("C-acao", cotacoes.JustificativaInput(justificativa="Justificativa válida"), BackgroundTasks(), _request(), user))


def test_analise_completa_accepts_director_ticket_exception_and_audits(monkeypatch):
    operation = {"id": "op-1", "status": "reprovada_triagem"}
    database = _db(_quote("capacidade_insuficiente:0", estagio="DOCUMENTADA", operation_id="op-1"), [operation])
    _patch(monkeypatch, database)
    user = {"id": "u-diretor", "role": "diretor", "alcada": "diretor"}

    result = asyncio.run(cotacoes.solicitar_analise_completa(
        "C-acao",
        cotacoes.JustificativaInput(valor_operacao=800_000, acima_ticket_maximo=True, justificativa="Exceção comercial aprovada"),
        BackgroundTasks(), _request(), user,
    ))

    assert result["status"] == "accepted"
    assert database.tables["operations"][0]["valor_operacao_relatorio"] == 800_000
    payload = database.tables["audit_trail"][0]["payload"]
    assert payload == {
        "cotacao_id": "C-acao", "alcada": "diretor", "justificativa": "Exceção comercial aprovada",
        "motivos": ["capacidade_insuficiente:0"], "excecao_ticket_maximo": True,
        "ticket_maximo": 700000, "enquadravel_contrato": 1_000_000.0, "valor_operacao": 800_000,
    }


@pytest.mark.parametrize(
    ("user", "payload", "status"),
    [
        ({"id": "u-gerente", "role": "gerente"}, cotacoes.JustificativaInput(valor_operacao=800_000, acima_ticket_maximo=True, justificativa="Exceção comercial aprovada"), 403),
        ({"id": "u-diretor", "role": "diretor"}, cotacoes.JustificativaInput(valor_operacao=800_000, acima_ticket_maximo=True, justificativa="curta"), 422),
        ({"id": "u-diretor", "role": "diretor"}, cotacoes.JustificativaInput(valor_operacao=1_000_001, acima_ticket_maximo=True, justificativa="Exceção comercial aprovada"), 422),
    ],
)
def test_analise_completa_rejects_invalid_ticket_exception(monkeypatch, user, payload, status):
    database = _db(_quote("capacidade_insuficiente:0", estagio="DOCUMENTADA", operation_id="op-1"), [{"id": "op-1", "status": "reprovada_triagem"}])
    _patch(monkeypatch, database)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(cotacoes.solicitar_analise_completa("C-acao", payload, BackgroundTasks(), _request(), user))
    assert exc.value.status_code == status
