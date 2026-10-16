"""Contratos puros do extrator financeiro; nao usam documentos reais."""

from app.services.pdf_texto import Deadline
import app.services.documentos_financeiros.service as financial_service

from app.services.documentos_financeiros.service import (
    SCHEMA_VERSAO,
    _consolidate,
    _empty_core_reason,
    _extract_with_truncation_recovery,
    _manifest,
    _num,
    _pages,
    _safe_payload,
    _single,
    _whitelist,
)


def test_payload_financeiro_remove_chaves_interpretadas_pelo_score_e_cpf():
    payload = _safe_payload(
        {
            "familia": "IRPF",
            "tipo": "IRPF",
            "nested": {
                "document_type": "x",
                "cpf": "123.456.789-09",
                "cpf_final": "12345678909",
            },
            "trecho": "CPF 123.456.789-09",
        }
    )
    assert "tipo" not in payload
    assert "document_type" not in payload["nested"]
    assert "cpf" not in payload["nested"]
    assert payload["nested"]["cpf_final"] == "909"
    assert "123.456.789-09" not in payload["trecho"]


def test_manifest_muda_quando_hash_capturado_muda():
    first = _manifest([{"sha256": "a" * 64}])
    second = _manifest([{"sha256": "a" * 64}, {"sha256": "b" * 64}])
    assert first != second


def test_consolidacao_nao_expoe_chaves_proibidas():
    result = _consolidate(
        [
            {
                "sha256": "a" * 64,
                "familia": "IRPF",
                "status": "EXTRAIDO",
                "payload": {"familia": "IRPF"},
            }
        ],
        "m",
    )

    def visit(value):
        if isinstance(value, dict):
            assert not {"tipo", "tipo_documento", "document_type"}.intersection(value)
            for item in value.values():
                visit(item)
        if isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)


def test_maybe_single_none_e_selecao_nao_obedece_paginas_da_classificacao():
    assert _single(None) is None
    pages = (
        ["capa"] * 3 + ["BALANCO: ativo passivo patrimônio líquido"] + ["anexo"] * 50
    )
    assert 4 in _pages(pages, "BALANCO_DRE", 10)


def test_irpf_whitelist_descarta_campos_proibidos_devolvidos_pelo_modelo():
    extracted = _whitelist(
        "IRPF",
        {
            "familia": "IRPF",
            "dados": {
                "nome": "Ana",
                "cpf_final": "12345678909",
                "endereco": "nao pode",
                "dados_bancarios": "nao pode",
            },
            "campo_extra": "nao pode",
        },
    )
    assert extracted["dados"]["cpf_final"] == "909"
    assert "campo_extra" not in extracted
    assert "endereco" not in extracted["dados"]


def test_schema_v2_separa_dinheiro_em_especie_de_aplicacoes():
    extracted = _whitelist(
        "IRPF",
        {
            "familia": "IRPF",
            "dados": {
                "bens_por_grupo": {
                    "especie": {"valor": 368500, "trecho": "06 10 DINHEIRO EM ESPECIE"},
                    "aplicacoes_depositos": {"valor": 0},
                }
            },
        },
    )
    assert SCHEMA_VERSAO == 2
    assert extracted["dados"]["bens_por_grupo"]["especie"]["valor"] == 368500
    assert extracted["dados"]["bens_por_grupo"]["aplicacoes_depositos"]["valor"] == 0


def test_consolidacao_escolhe_sped_e_calcula_indicadores():
    def evidence(value):
        return {"valor": value}

    balance = {
        "data_base": evidence("2025-12-31"),
        "natureza": "anual",
        "ativo_total": evidence(200),
        "passivo_circulante": evidence(50),
        "passivo_nao_circulante": evidence(50),
        "patrimonio_liquido": evidence(100),
        "ativo_circulante": evidence(80),
        "emprestimos_cp": evidence(20),
        "emprestimos_lp": evidence(30),
        "receita_liquida": evidence(120),
        "resultado_liquido": evidence(12),
    }
    worse = {**balance, "ativo_total": evidence(160)}
    rows = [
        {
            "sha256": "a" * 64,
            "familia": "BALANCO_DRE",
            "status": "EXTRAIDO",
            "payload": {"confianca_fonte": "MEDIA", "dados": {"exercicios": [worse]}},
        },
        {
            "sha256": "b" * 64,
            "familia": "SPED_ECD",
            "status": "EXTRAIDO",
            "payload": {"confianca_fonte": "ALTA", "dados": {"exercicios": [balance]}},
        },
    ]
    result = _consolidate(rows, "m", {"valor_operacao": 50})
    assert result["demonstracoes"][0]["fonte"]["ativo_total"] == "SPED_ECD"
    assert result["verificacoes"][0]["resultado"] == "OK"
    assert result["indicadores"]["liquidez_corrente"]["valor"] == 1.6
    assert result["divergencias"]


def test_recencia_prioriza_anual_recente_e_faturamento_mais_recente():
    def evidence(value):
        return {"valor": value}

    annual = {
        "data_base": evidence("2025-12-31"),
        "natureza": "anual",
        "ativo_total": evidence(100),
        "passivo_circulante": evidence(20),
        "passivo_nao_circulante": evidence(20),
        "patrimonio_liquido": evidence(60),
    }
    opening = {**annual, "data_base": evidence("2020-01-01"), "natureza": "abertura"}
    old_revenue = {
        "data_emissao": evidence("2025-01-01"),
        "meses": [],
        "total_declarado": evidence(10),
    }
    new_revenue = {
        "data_emissao": evidence("2025-12-01"),
        "meses": [],
        "total_declarado": evidence(20),
    }
    rows = [
        {
            "sha256": "a" * 64,
            "familia": "BALANCO_ABERTURA",
            "status": "EXTRAIDO",
            "payload": {"confianca_fonte": "ALTA", "dados": {"exercicios": [opening]}},
        },
        {
            "sha256": "b" * 64,
            "familia": "SPED_ECD",
            "status": "EXTRAIDO",
            "payload": {"confianca_fonte": "ALTA", "dados": {"exercicios": [annual]}},
        },
        {
            "sha256": "c" * 64,
            "familia": "FATURAMENTO",
            "status": "EXTRAIDO",
            "payload": {"dados": old_revenue},
        },
        {
            "sha256": "d" * 64,
            "familia": "FATURAMENTO",
            "status": "EXTRAIDO",
            "payload": {"dados": new_revenue},
        },
    ]
    result = _consolidate(rows, "manifest")
    assert result["demonstracoes"][0]["natureza"] == "anual"
    assert result["faturamento_12m"]["total_declarado"] == evidence(20)
    forbidden = {
        "receita_maior_recebido_uniao",
        "simples_cessao_mao_obra",
        "salto_escala_real",
        "dependencia_publica",
        "cobertura_aval",
    }
    assert not forbidden.intersection(item["codigo"] for item in result["verificacoes"])
    assert not forbidden.intersection(result["indicadores"])


def test_data_brasileira_ordena_demonstracao_e_ausente_nao_vira_zero():
    def evidence(value, **extra):
        return {"valor": value, **extra}

    old = {
        "data_base": evidence("31/12/2024"),
        "natureza": "anual",
        "ativo_total": evidence(100),
        "passivo_circulante": evidence(10),
        "passivo_nao_circulante": evidence(10),
        "patrimonio_liquido": evidence(80),
    }
    current = {**old, "data_base": evidence("31/12/2025")}
    rows = [
        {
            "sha256": "a" * 64,
            "familia": "SPED_ECD",
            "status": "EXTRAIDO",
            "payload": {"dados": {"exercicios": [old]}},
        },
        {
            "sha256": "b" * 64,
            "familia": "SPED_ECD",
            "status": "EXTRAIDO",
            "payload": {"dados": {"exercicios": [current]}},
        },
    ]
    result = _consolidate(rows, "manifest")
    assert result["demonstracoes"][0]["data_base"]["valor"] == "2025-12-31"
    assert _num(evidence(0, ausente_no_documento=True)) is None


def test_parcela_faltante_e_alavancagem_declarada():
    def evidence(value, **extra):
        return {"valor": value, **extra}

    balance = {
        "data_base": evidence("2025-12-31"),
        "natureza": "anual",
        "ativo_total": evidence(200),
        "passivo_circulante": evidence(50),
        "passivo_nao_circulante": evidence(50),
        "patrimonio_liquido": evidence(100),
    }
    debt = {
        "data_base": evidence("Setembro/2025"),
        "total_declarado": evidence(300),
        "credores": [
            {
                "saldo_devedor": evidence(300),
                "parcela_mensal": evidence(0, ausente_no_documento=True),
                "cessao_recebiveis": False,
            }
        ],
    }
    rows = [
        {
            "sha256": "a" * 64,
            "familia": "SPED_ECD",
            "status": "EXTRAIDO",
            "payload": {"dados": {"exercicios": [balance]}},
        },
        {
            "sha256": "b" * 64,
            "familia": "ENDIVIDAMENTO_DECLARADO",
            "status": "EXTRAIDO",
            "payload": {"dados": debt},
        },
    ]
    result = _consolidate(rows, "manifest")
    assert result["indicadores"]["servico_divida_sobre_faturamento"] == {
        "valor": None,
        "motivo": "parcelas_nao_informadas",
        "confianca": "BAIXA",
        "fonte": "ENDIVIDAMENTO_DECLARADO",
    }
    assert result["indicadores"]["alavancagem_declarada"]["valor"] == 3
    assert result["endividamento"]["data_base"]["valor"] == "2025-09-01"


def test_divergencia_capital_social_e_participacao_irpf():
    def evidence(value):
        return {"valor": value}

    balance = {
        "data_base": evidence("2025-12-31"),
        "natureza": "anual",
        "capital_social": evidence(100),
        "ativo_total": evidence(100),
        "passivo_circulante": evidence(10),
        "passivo_nao_circulante": evidence(10),
        "patrimonio_liquido": evidence(80),
    }
    rows = [
        {
            "sha256": "a" * 64,
            "familia": "SPED_ECD",
            "status": "EXTRAIDO",
            "payload": {"dados": {"exercicios": [balance]}},
        },
        {
            "sha256": "b" * 64,
            "familia": "CONTRATO_SOCIAL",
            "status": "EXTRAIDO",
            "payload": {
                "dados": {
                    "data_ultima_alteracao": evidence("2025-01-01"),
                    "capital_social": evidence(130),
                }
            },
        },
        {
            "sha256": "c" * 64,
            "familia": "IRPF",
            "status": "EXTRAIDO",
            "payload": {"dados": {"participacoes": [{"valor": evidence(50)}]}},
        },
    ]
    result = _consolidate(rows, "manifest")
    assert {item["campo"] for item in result["divergencias"]}.issuperset(
        {"capital_social", "participacao_socio_capital_social"}
    )


def test_truncamento_reextrai_demonstracao_por_exercicio(monkeypatch):
    def evidence(value):
        return {"valor": value}

    calls = []
    responses = iter(
        [
            (
                {"familia": "SPED_ECD", "dados": {"exercicios": []}},
                (1, 8000, 0.1),
                "max_tokens",
            ),
            (
                {
                    "familia": "SPED_ECD",
                    "dados": {
                        "exercicios": [
                            {"data_base": evidence("2024-12-31"), "natureza": "anual"}
                        ]
                    },
                },
                (1, 2, 0.01),
                "end_turn",
            ),
            (
                {
                    "familia": "SPED_ECD",
                    "dados": {
                        "exercicios": [
                            {"data_base": evidence("2025-12-31"), "natureza": "anual"}
                        ]
                    },
                },
                (1, 2, 0.01),
                "end_turn",
            ),
        ]
    )

    def fake_call(*args, **kwargs):
        calls.append(kwargs["max_tokens"])
        return next(responses)

    monkeypatch.setattr(financial_service, "_call_with_limit", fake_call)
    extracted, _, truncated = _extract_with_truncation_recovery(
        None,
        "SPED_ECD",
        {"ano_referencia": 2024},
        "balanco 2024 e balanco 2025",
        Deadline.start(30),
    )

    assert not truncated
    assert calls == [8000, 8000, 8000]
    assert len(extracted["dados"]["exercicios"]) == 2


def test_nucleo_vazio_e_parcial_mas_recibo_irpf_e_identificado():
    assert _empty_core_reason("SPED_ECD", {"exercicios": []}) == "demonstracoes_vazias"
    assert _empty_core_reason("FATURAMENTO", {"meses": []}) == "faturamento_sem_meses"
    dados = {"tem_recibo": True, "bens_por_grupo": {}, "participacoes": []}
    assert _empty_core_reason("IRPF", dados, {"tem_recibo_rfb": True}) is None
    assert dados["irpf_somente_recibo"] is True


def test_mes_ano_anual_vira_ultimo_dia_e_mesmo_exercicio_mescla_campos():
    def evidence(value, confidence="MEDIA"):
        return {"valor": value, "confianca": confidence}

    rows = [
        {
            "sha256": "a" * 64,
            "familia": "SPED_ECD",
            "status": "EXTRAIDO",
            "payload": {
                "classificacao": {"tem_recibo_rfb": True},
                "dados": {
                    "exercicios": [
                        {
                            "data_base": evidence("2024-12"),
                            "natureza": "anual",
                            "ativo_total": evidence(100, "ALTA"),
                        }
                    ]
                },
            },
        },
        {
            "sha256": "b" * 64,
            "familia": "BALANCO_DRE",
            "status": "EXTRAIDO",
            "payload": {
                "classificacao": {"assinado_contador": True},
                "dados": {
                    "exercicios": [
                        {
                            "data_base": evidence("2024-12-31"),
                            "natureza": "anual",
                            "resultado_liquido": evidence(20, "MEDIA"),
                        }
                    ]
                },
            },
        },
    ]
    demonstration = _consolidate(rows, "manifest")["demonstracoes"][0]
    assert demonstration["data_base"]["valor"] == "2024-12-31"
    assert demonstration["ativo_total"]["valor"] == 100
    assert demonstration["resultado_liquido"]["valor"] == 20
    assert demonstration["fonte"] == {
        "ativo_total": "SPED_ECD",
        "resultado_liquido": "BALANCO_DRE",
    }
