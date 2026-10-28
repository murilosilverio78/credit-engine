from app.services.contrato_cedido_service import contrato_cedido_view
from app.services.findings.version import EMITTER_VERSION
from tests.fakes.postgrest import Postgrest


def test_contract_view_projects_one_to_many_payment_and_risk_without_external_calls():
    database = Postgrest({
        "operations": [{"id": "op", "capacidade_memoria": {"n": 6}, "valor_enquadrado": 100_000}],
        "component_snapshots": [
            {"operation_id": "op", "component": "contratos_comprasnet", "status": "completed", "completed_at": "2026-10-01", "parsed_result": {"status_consulta": "ENCONTRADO", "contrato_comprasnet": {"numero": "00004/2025", "uasg": "123456", "valor_global": 1000, "vigencia_inicio": "2026-01-01", "modalidade_execucao": "CONTINUO"}, "faturas": [{"numero": "1", "dados_referencia": {"mesref": 9, "anoref": 2026}, "emissao": "2026-09-01", "data_liquidacao": "2026-09-10", "valor": "100,00", "valorliquido": "100,00"}, {"numero": "2", "dados_referencia": {"mesref": 9, "anoref": 2026}, "emissao": "2026-09-01", "data_liquidacao": "2026-09-10", "valor": "100,00", "valorliquido": "100,00"}], "historico": []}},
            {"operation_id": "op", "component": "pagamentos_portal", "status": "completed", "completed_at": "2026-10-02", "parsed_result": {"status_consulta": "ENCONTRADO", "filtro_usado": "UASG", "paginacao": {"parcial": False}, "ordens_bancarias": [{"documento": "123456OB1", "data": "2026-09-15", "valor": "200,00", "observacao": "CT 04/2025 NF 1 NF 2"}]}},
        ],
        "finding_runs": [{"id": "run", "operation_id": "op", "especialista": "fluxo_caixa", "versao_emissor": EMITTER_VERSION, "created_at": "2026-10-02"}],
        "findings": [{"run_id": "run", "codigo": "antecipacao_concorrente", "valor": {"ativa_mesmo_contrato": True, "operacoes": []}, "estado": "CONFIRMADO", "confianca": "ALTA", "pendencia": None}],
    })
    view = contrato_cedido_view("op", database=database)
    assert view["execucao"]["recebido"] == 200.0
    assert view["fluxo_mensal"][0]["valor_pago"] == 200.0
    assert view["risco"]["antecipacao_concorrente"]["valor"]["ativa_mesmo_contrato"] is True


def test_contract_view_uses_current_contract_value_not_cumulative_amendments():
    database = Postgrest({"operations": [{"id": "op"}], "component_snapshots": [{"operation_id": "op", "component": "contratos_comprasnet", "parsed_result": {"status_consulta": "ENCONTRADO", "contrato_comprasnet": {"valor_global": 150}, "valor_original": 100, "acrescimos_valor": 50, "faturas": [{"emissao": "2026-09-01", "valor": 20}]}}], "finding_runs": [], "findings": []})
    view = contrato_cedido_view("op", database=database)
    assert view["identificacao"]["valor_original"] == 100
    assert view["identificacao"]["valor_vigente"] == 150
    assert view["execucao"]["contratado"] == 150
    assert view["execucao"]["a_receber"] == 130.0


def test_contract_view_marks_missing_portal_findings_as_not_verified():
    database = Postgrest({"operations": [{"id": "op"}], "component_snapshots": [{"operation_id": "op", "component": "contratos_comprasnet", "status": "completed", "parsed_result": {"status_consulta": "ENCONTRADO", "contrato_comprasnet": {}}}, {"operation_id": "op", "component": "pagamentos_portal", "status": "completed", "parsed_result": {"status_consulta": "NAO_APLICAVEL"}}], "finding_runs": [], "findings": []})
    view = contrato_cedido_view("op", database=database)
    assert view["risco"]["antecipacao_concorrente"] is None
    assert view["qualidade"]["filtro_usado"] is None


def test_contract_view_uses_complete_portal_orders_when_comprasnet_has_no_invoices():
    database = Postgrest({
        "operations": [{"id": "op"}],
        "component_snapshots": [
            {"operation_id": "op", "component": "contratos_comprasnet", "parsed_result": {"status_consulta": "ENCONTRADO", "contrato_comprasnet": {"numero": "00027/2026", "uasg": "510678", "valor_global": 100_000, "vigencia_inicio": "2026-01-01"}, "faturas": []}},
            {"operation_id": "op", "component": "pagamentos_portal", "parsed_result": {"status_consulta": "ENCONTRADO", "paginacao": {"parcial": False}, "ordens_bancarias": [
                {"documento": "510678OB1", "data": "2026-10-19", "valor": "1.000,00", "observacao": "CONTR. 27/2026 COMP. 10/2026 DOC. FISCAI(S): 158"},
                {"documento": "510678DR1", "data": "2026-10-19", "valor": "100,00", "observacao": "ISSQN CONTR. 27/2026 COMP. 10/2026 DOC. FISCAI(S): 158"},
            ]}},
        ], "finding_runs": [], "findings": [],
    })

    view = contrato_cedido_view("op", database=database)

    assert view["execucao"]["fonte_execucao"] == "PORTAL_OB"
    assert view["execucao"]["recebido"] == 1_000
    assert view["execucao"]["faturado_bruto"] == 1_100
    assert view["execucao"]["alerta_sem_execucao"] is False


def test_contract_view_exposes_capacity_explanation():
    database = Postgrest({
        "operations": [{
            "id": "op", "capacidade_memoria": {
                "fonte_mensal": "SEM_EXECUCAO", "faturas_na_janela": 0,
                "janela_meses": 6, "mensal_nominal": 30_000,
            },
        }],
        "component_snapshots": [{
            "operation_id": "op", "component": "contratos_comprasnet",
            "parsed_result": {
                "status_consulta": "ENCONTRADO", "contrato_comprasnet": {},
            },
        }],
        "finding_runs": [], "findings": [],
    })

    view = contrato_cedido_view("op", database=database)

    assert view["risco"]["capacidade_explicacao"]["codigo"] == "SEM_EXECUCAO"


def test_contract_view_preserves_comprasnet_liquid_and_exposes_portal_received_separately():
    database = Postgrest({
        "operations": [{"id": "op"}],
        "component_snapshots": [
            {"operation_id": "op", "component": "contratos_comprasnet", "parsed_result": {"status_consulta": "ENCONTRADO", "contrato_comprasnet": {"numero": "00004/2025", "uasg": "123456", "valor_global": 1_000, "vigencia_inicio": "2026-01-01"}, "faturas": [{"numero": "1", "dados_referencia": {"mesref": 9, "anoref": 2026}, "emissao": "2026-09-01", "data_liquidacao": "2026-09-10", "valor": "100,00", "valorliquido": "100,00"}]}},
            {"operation_id": "op", "component": "pagamentos_portal", "parsed_result": {"status_consulta": "ENCONTRADO", "paginacao": {"parcial": False}, "ordens_bancarias": [{"documento": "123456OB1", "data": "2026-09-15", "valor": "90,00", "observacao": "CT 04/2025 NF 1"}]}},
        ],
        "finding_runs": [], "findings": [],
    })

    execution = contrato_cedido_view("op", database=database)["execucao"]

    assert execution["faturado_liquido"] == 100.0
    assert execution["fonte_faturado_liquido"] == "COMPRASNET_INFORMADO"
    assert execution["recebido_liquido_portal"] == 90.0
    assert execution["fonte_recebido_liquido_portal"] == "PAGAMENTOS_PORTAL"
