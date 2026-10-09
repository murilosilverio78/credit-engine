from app.services.report_pdf_service import cover_section, pricing_section, regularity_section


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


def test_pdf_cover_identifies_cnpja_as_cadastro_source():
    result = cover_section({}, {"brasil_api": {"parsed_result": {"fonte": "CNPJA_OPEN"}}}, {})
    assert "Fonte" in result
    assert "CNPJá" in result


def test_pdf_cover_falls_back_to_legacy_contract_balance_and_term():
    operation = {
        "cnpj": "31822605000191",
        "contrato_saldo": 300_000,
        "prazo_dias": 60,
    }

    result = cover_section(operation, {}, {})

    assert "R$ 300.000,00" in result
    assert "2,0 meses" in result


def test_pdf_uses_report_operation_value_and_preserves_quote_value():
    operation = {
        "cnpj": "31822605000191",
        "valor_solicitado": 500_000,
        "valor_enquadrado": 450_000,
        "valor_operacao_relatorio": 300_000,
        "taxa_breakdown": {
            "detalhes": {
                "valor_operacao_rs": 300_000,
                "total_receita_rs": 42_000,
            }
        },
    }

    cover = cover_section(operation, {}, {})
    pricing = pricing_section(operation)

    assert "Valor da operação (precificação)" in cover
    assert "R$ 300.000,00 · cotação: R$ 500.000,00" in cover
    assert "Calculado sobre o valor da operação: R$ 300.000,00" in pricing
    assert "R$ 42.000,00" in pricing


def test_pdf_without_report_operation_value_keeps_existing_conference():
    operation = {"valor_solicitado": 500_000, "valor_enquadrado": 450_000}

    result = cover_section(operation, {}, {})

    assert "Valor da operação (precificação)" not in result
    assert "Valor solicitado" in result


def test_pdf_explains_balance_catalog_presence_without_document_analysis():
    result = regularity_section({"flags": ["balanco_via_catalogo_broadfactor"]})

    assert "Balanço considerado presente pelo catálogo da Broadfactor (não analisado)." in result


def test_pdf_labels_default_term_when_contract_was_not_verified():
    operation = {
        "cnpj": "14757507000107",
        "prazo_final_meses": 12,
        "fonte_prazo_vincendo": "DEFAULT",
    }
    snapshots = {
        "contratos_comprasnet": {
            "parsed_result": {"status_consulta": "NAO_VERIFICADO"}
        }
    }

    result = cover_section(operation, snapshots, {})

    assert "12 meses (prazo padrão, contrato não verificado)" in result
