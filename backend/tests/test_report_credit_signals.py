from app.services.report_pdf_service import cover_section


def test_pdf_cover_shows_operation_conference_and_concentration():
    operation = {
        "cnpj": "31822605000191",
        "razao_social": "Empresa Teste",
        "rating": "B",
        "score": 78,
        "valor_solicitado": 500_000,
        "valor_enquadrado": 450_000,
        "saldo_vincendo": 916_444.47,
        "contrato_saldo": None,
        "prazo_final_meses": 2,
        "prazo_dias": None,
        "fonte_prazo_vincendo": "COMPRASNET",
        "taxa_sugerida": 0.025,
    }
    snapshots = {
        "recursos_recebidos": {
            "parsed_result": {
                "concentracao": {
                    "hhi": 10_000,
                    "faixa": "CONCENTRADO",
                    "n_orgaos": 1,
                    "top_orgao": "Ministério da Saúde",
                    "top_participacao": 1,
                }
            }
        }
    }

    result = cover_section(operation, snapshots, {})

    assert "Conferência da operação" in result
    assert "R$ 500.000,00" in result
    assert "R$ 450.000,00" in result
    assert "R$ 916.444,47" in result
    assert "2 meses (Comprasnet)" in result
    assert "2,50%" in result
    assert "100,0% da receita vem de Ministério da Saúde" in result
    assert "HHI 10.000,0" in result
    assert "CONCENTRADO" in result


def test_pdf_cover_falls_back_to_legacy_contract_balance_and_term():
    operation = {
        "cnpj": "31822605000191",
        "contrato_saldo": 300_000,
        "prazo_dias": 60,
    }

    result = cover_section(operation, {}, {})

    assert "R$ 300.000,00" in result
    assert "2,0 meses" in result
