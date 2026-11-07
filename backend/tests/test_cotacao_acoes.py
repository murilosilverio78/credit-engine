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
    return {"cotacao_id": "C-acao", "cnpj": "00000000000100", "estagio": "LISTA_ESPERA", "estagio_max": "LISTA_ESPERA", "estagio_motivo": motivo, "valor_enquadrado": 900000, "valor_solicitado": 950000, "n_documentos": 0, **extra}


def _db(quote, operations=None, snapshots=None):
    database = Postgrest({"cotacoes_broadfactor": [quote], "operations": operations or [], "component_snapshots": snapshots or [], "audit_trail": []})
    return database


def _patch(monkeypatch, database):
    monkeypatch.setattr(cotacoes, "supabase", database)
    monkeypatch.setattr(audit_service, "supabase", database)
    monkeypatch.setattr(cotacoes, "_params", lambda: {"ticket_maximo": 700000, "ticket_minimo": 50000})


def test_reenquadrar_updates_stage_audits_and_is_idempotent(monkeypatch):
    database = _db(_quote())
    _patch(monkeypatch, database)
    user = {"id": "u-1", "role": "analista", "alcada": "analista"}
    result = asyncio.run(cotacoes.reenquadrar_cotacao("C-acao", _request(), user))
    assert result["valor_enquadrado"] == 700000
    assert database.tables["cotacoes_broadfactor"][0]["estagio"] == "ENQUADRADA"
    assert database.tables["audit_trail"][0]["action"] == "quote_reframed"
    assert asyncio.run(cotacoes.reenquadrar_cotacao("C-acao", _request(), user))["idempotent"] is True


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
