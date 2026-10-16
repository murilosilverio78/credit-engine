from app.services.findings.adapters.financeiro import emit_financeiro
from app.services.findings.schemas import Confianca, Estado
from app.services.documentos_financeiros.service import _confidence, _consolidate


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


def test_vhg_payload_preserves_financial_provenance_and_confidence():
    evidence = lambda value, confidence: {"valor": value, "confianca": confidence}
    assert _confidence({"familia": "SPED_ECD", "tem_recibo_rfb": True}) == "ALTA"
    assert _confidence({"familia": "IRPF", "tem_recibo_rfb": True}) == "ALTA"
    assert _confidence({"familia": "ECF", "tem_recibo_rfb": True}) == "ALTA"
    assert _confidence({"familia": "FATURAMENTO", "assinado_contador": True}) == "MEDIA"
    sped = {
        "data_base": evidence("2025-12-31", "ALTA"),
        "natureza": "anual",
        "ativo_circulante": evidence(150, "ALTA"),
        "ativo_total": evidence(220, "ALTA"),
        "passivo_circulante": evidence(50, "ALTA"),
        "passivo_nao_circulante": evidence(70, "ALTA"),
        "emprestimos_cp": evidence(20, "ALTA"),
        "emprestimos_lp": evidence(30, "ALTA"),
        "patrimonio_liquido": evidence(100, "ALTA"),
        "capital_social": evidence(100, "ALTA"),
        "receita_liquida": evidence(120, "ALTA"),
        "resultado_liquido": evidence(12, "ALTA"),
    }
    contador = {
        "data_base": evidence("2025-12-31", "MEDIA"),
        "natureza": "anual",
        "capital_social": evidence(200, "MEDIA"),
    }
    rows = [
        {
            "sha256": "a" * 64,
            "familia": "SPED_ECD",
            "status": "EXTRAIDO",
            "payload": {"confianca_fonte": "ALTA", "dados": {"exercicios": [sped]}},
        },
        {
            "sha256": "b" * 64,
            "familia": "BALANCO_DRE",
            "status": "EXTRAIDO",
            "payload": {"confianca_fonte": "MEDIA", "dados": {"exercicios": [contador]}},
        },
        {
            "sha256": "c" * 64,
            "familia": "FATURAMENTO",
            "status": "EXTRAIDO",
            "payload": {
                "confianca_fonte": "MEDIA",
                "dados": {
                    "meses": [{"valor": evidence(120, "MEDIA")}],
                    "total_declarado": evidence(120, "MEDIA"),
                    "data_emissao": evidence("2026-10-01", "MEDIA"),
                },
            },
        },
        {
            "sha256": "d" * 64,
            "familia": "IRPF",
            "status": "EXTRAIDO",
            "payload": {
                "confianca_fonte": "ALTA",
                "dados": {
                    "nome": "Avalista VHG",
                    "cpf_final": "123",
                    "bens_direitos_total_atual": evidence(100, "ALTA"),
                    "dividas_onus_atual": evidence(0, "ALTA"),
                    "bens_por_grupo": {},
                    "participacoes": [],
                },
            },
        },
    ]

    payload = _consolidate(rows, "vhg-manifest")
    findings = {
        item.codigo: item
        for item in emit_financeiro(
            {"documentos_financeiros": payload, "contratos_pncp": {}},
            fingerprint="vhg",
            operation={"valor_enquadrado": 100, "cnpj": "12345678000100"},
        )
    }

    assert payload["indicadores"]["liquidez_corrente"] == {
        "valor": 3,
        "motivo": None,
        "confianca": "ALTA",
        "fonte": "SPED_ECD",
    }
    assert payload["faturamento_12m"]["confianca"] == "MEDIA"
    assert payload["faturamento_12m"]["sha256"] == "c" * 64
    assert payload["avalistas"][0]["confianca"] == "ALTA"
    assert payload["avalistas"][0]["sha256"] == "d" * 64
    assert payload["avalistas"][0]["familia"] == "IRPF"
    assert findings["liquidez_corrente"].confianca == Confianca.ALTA
    assert findings["receita_declarada_12m"].confianca == Confianca.MEDIA
    assert findings["aval_cobertura"].confianca == Confianca.ALTA
    assert findings["divergencia_documental"].confianca in {
        Confianca.ALTA,
        Confianca.MEDIA,
    }
