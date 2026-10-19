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


def test_policy_view_formats_real_operation_02295223_informativos():
    """Valores da 02295223 / operação 271d9f08, sem expor JSON na leitura."""
    result = {
        "status_decisao": "DADOS_INSUFICIENTES",
        "faltantes": ["porte", "sacado_orgao", "cadastro_regularidade"],
        "alcada_minima": 3,
        "alcada_motivos": [{"codigo": "rating", "nivel": 2, "mensagem": "Rating D"}, {"codigo": "exposicao_sobre_pl", "nivel": 3, "mensagem": "Exposição sobre PL"}],
        "condicoes_precedentes": [{"codigo": "capital_social_alterado_recente", "descricao": "Comprovar integralização do capital", "documento_exigido": "Comprovante"}],
        "informativos": [
            {"codigo": "aval_cobertura", "valor": {"cobertura": 0.085, "pl_ajustado": 8_180_000, "pl_declarado": 9_000_000}},
            {"codigo": "aval_patrimonio_nao_verificavel", "valor": [{"nome": "Ana", "cpf_final": "1234", "pct_total": 0, "pct_especie": 0}, {"nome": "Bia", "cpf_final": "5678", "pct_total": 0.5, "pct_especie": 0.2, "pct_quotas_cedente": 0.6}, {"nome": "Caio", "cpf_final": "9012", "pct_total": 0.5, "pct_especie": 0.2, "pct_quotas_cedente": 0.2}]},
            {"codigo": "dependencia_publica", "valor": 0.625},
            {"codigo": "divergencia_documental", "valor": [{"campo": "participacao_socio_capital_social", "fonte_a": "irpf", "valor_a": 480_000, "fonte_b": "contrato_social", "valor_b": 2_000_000}, {"campo": "margem_pct", "fonte_a": "dre", "valor_a": 0.084, "fonte_b": "balanco", "valor_b": 0.1}]},
            {"codigo": "margem_liquida", "valor": 0.084},
            {"codigo": "receita_declarada_12m", "valor": 8_180_000},
        ],
    }
    db = _database(result)
    db.tables["findings"].extend(
        {"operation_id": "op-1", "run_id": "run-1", "codigo": code, "estado": "CONFIRMADO", "confianca": "ALTA", "valor": value, "evidencia": []}
        for code, value in [(item["codigo"], item["valor"]) for item in result["informativos"]]
    )
    db.tables["finding_catalog"].extend({"codigo": item["codigo"], "descricao": item["codigo"].replace("_", " ").capitalize()} for item in result["informativos"])

    view = latest_policy_view("op-1", database=db)

    assert [item["texto"] for item in view["decisao"]["faltantes"]] == [
        "porte e operacionalidade (sai no relatório completo)", "sacado e órgão", "cadastro e regularidade"
    ]
    assert [item["texto"] for item in view["decisao"]["alcada_motivos"]] == ["Nível 2: Rating D", "Nível 3: Exposição sobre PL"]
    assert view["condicoes_precedentes"][0]["texto"] == "Comprovar integralização do capital"
    assert [item["texto"] for item in view["informativos"]] == [
        "Aval cobre 8,5% da operação (patrimônio ajustado R$ 8,18 mi de R$ 9,00 mi declarados)",
        "Ana (CPF final 1234): sem patrimônio comprovado",
        "Bia (CPF final 5678): 60% do patrimônio são quotas da própria empresa",
        "Caio (CPF final 9012): patrimônio majoritariamente não líquido",
        "62% da receita vem de pagamentos públicos",
        "Participação do sócio no capital: IRPF do sócio diz R$ 480.000,00, contrato social diz R$ 2,00 mi",
        "Margem pct: DRE diz 8,4%, balanço diz 10,0%",
        "Margem líquida de 8,4%",
        "Receita declarada nos últimos 12 meses: R$ 8,18 mi",
    ]
    assert all("{" not in item["texto"] for item in view["informativos"])


def test_policy_view_exposes_fluxo_caixa_as_informative_without_policy_rule():
    db = _database({"status_decisao": "DECISAO_CALCULADA"})
    db.tables["finding_runs"] = [{"id": "cash-run", "operation_id": "op-1", "especialista": "fluxo_caixa", "created_at": "2026-10-19"}]
    db.tables["findings"].append({"operation_id": "op-1", "run_id": "cash-run", "codigo": "empenho_insuficiente", "estado": "CONFIRMADO", "confianca": "ALTA", "valor": {"valor_descoberto": 51_520.26, "meses_descobertos": 2}, "evidencia": []})
    db.tables["finding_catalog"].append({"codigo": "empenho_insuficiente", "descricao": "Empenho insuficiente"})

    view = latest_policy_view("op-1", database=db)

    assert view["informativos"][0]["texto"] == "Empenho insuficiente: R$ 51.520,26 descobertos em 2 meses"


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
