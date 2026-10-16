from app.services.findings.adapters.financeiro import emit_financeiro
from app.services.findings.schemas import Confianca, Estado


def _financeiro_payload():
    return {
        "documentos": [
            {"familia": "SPED_ECD", "status": "EXTRAIDO", "confianca_fonte": "ALTA"},
            {"familia": "FATURAMENTO", "status": "EXTRAIDO", "confianca_fonte": "ALTA"},
            {"familia": "IRPF", "status": "EXTRAIDO", "confianca_fonte": "ALTA"},
        ],
        "demonstracoes": [
            {
                "fonte": "SPED_ECD",
                "confianca": "ALTA",
                "patrimonio_liquido": {"valor": 100, "confianca": "ALTA"},
            }
        ],
        "verificacoes": [],
        "indicadores": {
            "liquidez_corrente": {"valor": 1.5, "confianca": "ALTA"},
            "alavancagem_declarada": {"valor": None, "motivo": "sem_dados"},
            "servico_divida_sobre_faturamento": {"valor": None, "motivo": "sem_dados"},
            "margem_liquida": {"valor": None, "motivo": "sem_dados"},
        },
        "faturamento_12m": {"soma_calculada": 120, "confianca_fonte": "ALTA"},
        "endividamento": {"fontes": []},
        "avalistas": [
            {
                "nome": "Ana Avalista",
                "cpf_final": "123",
                "confianca_fonte": "ALTA",
                "bens_por_grupo": {},
                "participacoes": [],
                "patrimonio_liquido_declarado": 100,
            }
        ],
        "societario": {"socios": []},
    }


def test_financial_findings_use_evidence_confidence_and_identify_avalista():
    findings = {
        item.codigo: item
        for item in emit_financeiro(
            {"documentos_financeiros": _financeiro_payload(), "contratos_pncp": {}},
            fingerprint="test",
            operation={"valor_enquadrado": 100, "cnpj": "12345678000100"},
        )
    }

    assert findings["liquidez_corrente"].confianca == Confianca.ALTA
    assert findings["salto_escala_real"].estado == Estado.NAO_VERIFICADO
    assert findings["salto_escala_real"].pendencia == {
        "motivo": "contratos_pncp_nao_concluido"
    }
    assert findings["aval_patrimonio_nao_verificavel"].valor == [
        {
            "nome": "Ana Avalista",
            "cpf_final": "123",
            "pct_especie": 0,
            "pct_quotas_cedente": 0,
            "pct_total": 0,
        }
    ]
    assert all(
        item.pendencia and item.pendencia.get("motivo")
        for item in findings.values()
        if item.estado == Estado.NAO_VERIFICADO
    )
    assert all(
        item.confianca == Confianca.BAIXA
        for item in findings.values()
        if item.estado == Estado.NAO_VERIFICADO
    )
