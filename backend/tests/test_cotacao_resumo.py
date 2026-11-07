import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import cotacoes
from app.api.v1.endpoints.cotacoes import montar_resumo
from tests.fakes.postgrest import Postgrest


@pytest.fixture(autouse=True)
def _fixed_eligibility_parameters(monkeypatch):
    """Mantém os fixtures unitários isolados de Supabase."""
    monkeypatch.setattr(cotacoes, "_params", lambda: {"ticket_minimo": 50_000, "ticket_maximo": 700_000, "pct_max_contrato": 0.5})


def _quote(cotacao_id, stage, motivo=None, **extra):
    return {
        "cotacao_id": cotacao_id, "cnpj": "00000000000100", "nome_fornecedor": "Fornecedor teste",
        "estagio": stage, "estagio_max": stage, "estagio_motivo": motivo,
        "valor_solicitado": 100_000, "valor_enquadrado": 90_000, "saldo_vincendo": 180_000,
        "data_expiracao": "2026-11-25", "data_cotacao": "2026-11-04", "n_documentos": 0,
        **extra,
    }


def _statuses(*components):
    return {component: "completed" for component in components}


def _orbenk_snapshots():
    return {
        "brasil_api": {"situacao_cadastral": "ATIVA"},
        "pessoa_juridica": {"possui_sancao": True},
        "ceis": {"total_registros": 1, "registros": [{"id": 388292, "dataFimSancao": "05/05/2027", "dataInicioSancao": "06/05/2026", "orgaoSancionador": {"nome": "Prefeitura Municipal de Porto Alegre (RS)", "esfera": "MUNICIPAL", "siglaUf": "RS"}, "tipoSancao": {"descricaoPortal": "Impedimento/proibição de contratar com prazo determinado", "descricaoResumida": "Impedimento"}, "fundamentacao": [{"codigo": "LEI_14133", "descricao": "Lei 14.133, art. 156, III"}], "abrangenciaDefinidaDecisaoJudicial": "Todos os Poderes da esfera do órgão sancionador", "numeroProcesso": "26.0.000009506-0", "textoPublicacao": "DOM Porto Alegre, seção 7782, p. 53"}]},
        "cnep": {"total_registros": 0, "registros": []}, "cepim": {"total_registros": 0, "registros": []},
        "acordos_leniencia": {"total_acordos": 0, "acordos": []},
        "contratos_pncp": {"contrato_cedido": {"valor_global": 183600, "data_inicio_vigencia": "2025-09-15", "data_fim_vigencia": "2026-09-15"}},
        "contratos_comprasnet": {"status_consulta": "ENCONTRADO", "contrato_comprasnet": {"valor_global": 214732.80, "vigencia_inicio": "2025-09-15", "vigencia_fim": "2027-09-15", "prazo_vincendo_meses": 10}},
        "pagamentos_portal": {"status_consulta": "ENCONTRADO", "ordens_bancarias": []},
    }


def test_apollo_ticket_maximo_summary():
    summary = montar_resumo(
        _quote("C-1793791899193", "LISTA_ESPERA", "acima_ticket_maximo", valor_solicitado=1_900_000, valor_enquadrado=1_626_770.44, saldo_vincendo=3_253_540.87),
        None, {}, {},
    )
    assert summary["selo"]["classe"] == "COMERCIAL"
    assert summary["motivos"][0]["titulo"] == "Valor acima do ticket máximo"
    assert {action["id"] for action in summary["acoes"]} == {"arquivar"}
    assert summary["consultas"]["titulo"] == "REGRAS APLICADAS (SEM CUSTO)"


def test_jjx_no_documents_summary():
    summary = montar_resumo(_quote("C-1793887270802", "ENQUADRADA", "sem_documentos_broadfactor"), None, {}, {})
    assert summary["selo"]["classe"] == "CLIENTE"
    assert len(summary["motivos"][0]["evidencias"]) == 6
    assert {action["id"] for action in summary["acoes"]} == {"pedir_documentos", "consultas_gratuitas"}
    assert "Equipe AntecipaGov" in next(action for action in summary["acoes"] if action["id"] == "pedir_documentos")["mensagem"]


def test_orbenk_veto_and_contract_divergence_summary():
    snapshots = _orbenk_snapshots()
    statuses = _statuses(*snapshots)
    operation = {"id": "aa8d8ae1", "status": "reprovada_triagem", "capacidade_contrato": 0}
    quote = _quote("C-1793047365028", "DOCUMENTADA", "Sancao ativa em CEIS; capacidade_insuficiente:0", operation_id="aa8d8ae1")
    summary = montar_resumo(quote, operation, snapshots, statuses)
    assert summary["selo"]["classe"] == "VETO"
    assert summary["motivos"][0]["codigo"] == "SANCAO_CEIS"
    assert summary["motivos"][1]["classe"] == "REVISAR_DADO"
    sanction = summary["motivos"][0]
    evidence = {item["rotulo"]: item["valor"] for item in sanction["evidencias"]}
    assert evidence["Órgão sancionador"] == "Prefeitura Municipal de Porto Alegre (RS) · esfera municipal"
    assert evidence["Fundamento"] == "Lei 14.133, art. 156, III"
    assert evidence["Processo e publicação"] == "26.0.000009506-0 · DOM Porto Alegre, seção 7782, p. 53"
    assert all(isinstance(value, str) and value != "—" for value in evidence.values())
    assert "não alcança contratos com a União" in sanction["texto"]
    assert next(row for row in summary["consultas"]["itens"] if row["nome"] == "CEIS")["resultado"] == "1 sanção ativa"
    assert next(row for row in summary["consultas"]["itens"] if row["nome"] == "Contrato no Comprasnet")["tom"] == "atencao"
    full = next(action for action in summary["acoes"] if action["id"] == "enviar_analise_completa")
    assert full["habilitada"] is False and full["motivo_bloqueio"] == "Bloqueado: existe veto ativo"
    refusal = next(action for action in summary["acoes"] if action["id"] == "copiar_mensagem_recusa")
    assert "Prefeitura Municipal de Porto Alegre" in refusal["mensagem"] and "Equipe AntecipaGov" in refusal["mensagem"]
    assert summary["parada"]["resumo"] == "2 motivos; Sanção ativa no CEIS prevalece. Há um veto definitivo: nenhuma ação libera a operação antes de 05/05/2027."


def test_proclima_qualified_summary():
    operation = {"id": "op-1", "status": "aguardando_relatorio"}
    summary = montar_resumo(_quote("C-1793367917902", "QUALIFICADA", None, operation_id="op-1", n_documentos=19), operation, {}, {})
    assert summary["selo"]["classe"] == "EM_ANDAMENTO"
    assert summary["motivos"] == []
    assert summary["acoes"][0]["id"] == "gerar_relatorio"


def test_green_house_closed_summary():
    operation = {"id": "op-2", "status": "cotacao_encerrada"}
    summary = montar_resumo(_quote("C-1793382852614", "ENCERRADA", "cotacao_ausente_na_listagem", operation_id="op-2", estagio_max="QUALIFICADA", n_documentos=27), operation, {}, {})
    assert summary["selo"]["classe"] == "ENCERRADA"
    assert summary["trilha"]["estagio_max"] == "QUALIFICADA"
    assert {action["id"] for action in summary["acoes"]} == {"abrir_analise", "avisar_comercial"}
    assert summary["kpis"][2] == {"rotulo": "Expiraria em", "valor": "2026-11-25", "formato": "DATA", "nota": "saiu antes do prazo"}
    assert summary["motivos"][0]["evidencias"][0]["valor"] == "Qualificadas"


def test_list_rules_use_real_type_and_minimum_ticket_reason():
    summary = montar_resumo(
        _quote("C-tipo", "LISTA_ESPERA", "tipo_nao_elegivel:-; abaixo_ticket_minimo", tipo="-"),
        None, {}, {}, parametros={"ticket_minimo": 50_000},
    )
    rows = {row["nome"]: row for row in summary["consultas"]["itens"]}
    assert rows["Tipo de recebível"] == {"nome": "Tipo de recebível", "coleta": "regra", "resultado": "-", "tom": "ruim"}
    assert rows["Valor"]["resultado"] == "abaixo do ticket mínimo"
    assert rows["Valor"]["tom"] == "ruim"
    assert "Documentos Broadfactor" not in rows


def test_legacy_ticket_reason_only_offers_archive_and_enquadrada_shows_documents():
    summary = montar_resumo(
        _quote("C-apollo", "LISTA_ESPERA", "acima_ticket_maximo", tipo="CONTRATO", valor_enquadrado=1_626_770.44),
        None, {}, {}, parametros={"ticket_maximo": 700_000},
    )
    assert [action["id"] for action in summary["acoes"]] == ["arquivar"]
    enquadrada = montar_resumo(_quote("C-jjx", "ENQUADRADA", "sem_documentos_broadfactor", tipo="CONTRATO"), None, {}, {})
    assert any(row["nome"] == "Documentos Broadfactor" for row in enquadrada["consultas"]["itens"])


@pytest.mark.parametrize(
    ("quote", "operation", "snapshots", "expected"),
    [
        (_quote("C-1793791899193", "LISTA_ESPERA", "acima_ticket_maximo", valor_solicitado=1_900_000, valor_enquadrado=1_626_770.44), None, {}, "COMERCIAL"),
        (_quote("C-1793887270802", "ENQUADRADA", "sem_documentos_broadfactor"), None, {}, "CLIENTE"),
        (_quote("C-1793047365028", "DOCUMENTADA", "Sancao ativa em CEIS; capacidade_insuficiente:0", operation_id="op-orbenk"), {"id": "op-orbenk", "status": "reprovada_triagem", "capacidade_contrato": 0}, _orbenk_snapshots(), "VETO"),
        (_quote("C-1793367917902", "QUALIFICADA", None, operation_id="op-proclima", n_documentos=19), {"id": "op-proclima", "status": "aguardando_relatorio"}, {}, "EM_ANDAMENTO"),
        (_quote("C-1793382852614", "ENCERRADA", "cotacao_ausente_na_listagem", operation_id="op-green", estagio_max="QUALIFICADA", n_documentos=27), {"id": "op-green", "status": "cotacao_encerrada"}, {}, "ENCERRADA"),
    ],
    ids=["apollo", "jjx", "orbenk", "proclima", "green-house"],
)
def test_resumo_endpoint_five_acceptance_cases(monkeypatch, quote, operation, snapshots, expected):
    """The approved examples are API fixtures, including quotes without operation_id."""
    snapshot_rows = [
        {"operation_id": quote.get("operation_id"), "component": component, "status": "completed", "parsed_result": parsed}
        for component, parsed in snapshots.items()
    ]
    database = Postgrest({
        "cotacoes_broadfactor": [quote],
        "operations": [operation] if operation else [],
        "component_snapshots": snapshot_rows,
    })
    monkeypatch.setattr(cotacoes, "supabase", database)
    monkeypatch.setattr(cotacoes, "_params", lambda: {"ticket_maximo": 700_000})
    app = FastAPI()
    app.include_router(cotacoes.router, prefix="/api/v1/cotacoes")
    response = TestClient(app).get(f"/api/v1/cotacoes/{quote['cotacao_id']}/resumo")

    assert response.status_code == 200
    assert response.json()["cotacao_id"] == quote["cotacao_id"]
    assert response.json()["selo"]["classe"] == expected
