import asyncio

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import operations
from app.services.policy_presentation_service import latest_policy_view
from tests.fakes.postgrest import Postgrest


def _database(result, *, policy_status="SOMBRA"):
    return Postgrest({
        "operations": [{"id": "op-1", "cnpj": "12345678000199", "rating": "B", "limite_aprovado": 1000}],
        "policy_versions": [{"id": "policy-2", "versao": 2, "status": policy_status}],
        "policy_shadow_runs": [{
            "operation_id": "op-1", "policy_version_id": "policy-2", "created_at": "2026-10-19T10:00:00Z", "data_referencia": "2026-10-19",
            "runs_usados": {"cadastro_regularidade": {"run_id": "run-1"}},
            "resultado": result,
            "oficial": {"rating": "B", "limite_aprovado_rs": 1000},
        }],
        "findings": [{"operation_id": "op-1", "run_id": "run-1", "codigo": "capital_social_alterado_recente", "estado": "CONFIRMADO", "confianca": "ALTA", "valor": {"tipo": "IRPF"}, "evidencia": [{"sha256": "a" * 64, "pagina": 2, "trecho": "assinatura"}]}],
        "finding_catalog": [{"codigo": "capital_social_alterado_recente", "descricao": "Capital social alterado"}],
        "documentos_cliente": [{"id": "doc-1", "cnpj": "12345678000199", "nome_arquivo": "balanco.pdf", "tipo": "ULTIMO_BALANCO", "sha256": "a" * 64, "capturado_em": "2026-10-01"}],
    })


def test_policy_view_enriches_cp_with_source_document():
    db = _database({"status_decisao": "DECISAO_CALCULADA", "rating": "C", "limite_aprovado_rs": 850, "alcada_minima": 2, "alcada_motivos": [], "condicoes_precedentes": [{"codigo": "capital_social_alterado_recente", "descricao": "Integralizar", "documento_exigido": "Balanço"}], "limite_multiplicadores": [{"codigo": "capital_social_alterado_recente", "fator": 0.85}], "taxa_ajuste_pp": 0.2, "informativos": []})

    view = latest_policy_view("op-1", database=db)

    evidence = view["condicoes_precedentes"][0]["finding"]["evidencia"][0]["documento"]
    assert view["politica"]["status"] == "SOMBRA"
    assert evidence == {"documento_id": "doc-1", "nome_arquivo": "balanco.pdf", "familia": "ULTIMO_BALANCO", "data_base": "2026-10-01", "pagina": 2, "trecho": "assinatura"}


@pytest.mark.parametrize("decision", [
    {"status_decisao": "DADOS_INSUFICIENTES", "faltantes": ["porte"]},
    {"status_decisao": "VETO", "bloqueios": ["capital_social_alterado_recente"]},
])
def test_policy_view_exposes_insufficient_and_veto(decision):
    view = latest_policy_view("op-1", database=_database(decision))
    assert view["decisao"]["status_decisao"] == decision["status_decisao"]


def test_policy_view_returns_empty_without_shadow_run():
    db = _database({})
    db.tables["policy_shadow_runs"] = []
    assert latest_policy_view("op-1", database=db) == {"status": "SEM_AVALIACAO"}


def test_document_url_rejects_document_from_another_cnpj(monkeypatch):
    db = Postgrest({
        "operations": [{"id": "op-1", "cnpj": "12345678000199"}],
        "documentos_cliente": [{"id": "doc-other", "cnpj": "99887766000155", "storage_key": "private/key"}],
    })
    monkeypatch.setattr(operations, "supabase", db)
    monkeypatch.setattr(operations, "_operation_snapshot", lambda _id: {"id": "op-1", "cnpj": "12345678000199"})

    with pytest.raises(HTTPException) as error:
        asyncio.run(operations.get_operation_document_url("op-1", "doc-other", {"role": "analista"}))

    assert error.value.status_code == 404
