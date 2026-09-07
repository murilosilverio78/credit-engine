import os
from datetime import date
from types import SimpleNamespace


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.integrations.broadfactor.client import Recebimento  # noqa: E402
from app.workers.tasks import contratos as contratos_portal  # noqa: E402
from app.workers.tasks import contratos_comprasnet  # noqa: E402


CNPJ = "02.955.426/0001-24"
TODAY = date(2026, 9, 6)


def receipt(value, uasg):
    return Recebimento(
        valor=value,
        orgao="Orgao",
        codigo_orgao="36000",
        orgao_superior=None,
        unidade_gestora="UG",
        competencia="08/2026",
        codigo_ug=uasg,
    )


def raw_contract(cnpj=CNPJ, contract_id=42):
    return {
        "id": contract_id,
        "numero": "00042/2026",
        "contratante": {
            "orgao_origem": {
                "nome": "INSTITUTO FEDERAL",
                "unidade_gestora_origem": {
                    "codigo": "158132",
                    "nome_resumido": "IFMS",
                },
            }
        },
        "fornecedor": {"cnpj_cpf_idgener": cnpj, "nome": "Fornecedor"},
        "vigencia_inicio": "2026-01-01",
        "vigencia_fim": "2027-03-15",
        "valor_inicial": "488.000,00",
        "valor_global": "500.000,00",
        "valor_parcela": "50.000,00",
        "num_parcelas": 10,
        "situacao": "Ativo",
        "prorrogavel": "Sim",
        "objeto": "Servico continuado",
    }


class FakeComprasnet:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        response = self.responses.get(path, [])
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


def test_receipt_maps_code_ug_and_orders_by_received_value():
    parsed = Recebimento.de_json(
        {
            "value": 125,
            "nameOrganization": "Orgao",
            "organizationCode": "36000",
            "codeUg": 158132,
            "competency": "08/2026",
        }
    )

    assert parsed.codigo_orgao == "36000"
    assert parsed.codigo_ug == "158132"
    assert contratos_comprasnet._ordered_uasgs(
        [receipt(100, "158100"), receipt(200, "158132"), receipt(50, "158100")]
    ) == ["158132", "158100"]


def test_portal_contract_mapping_adds_codes_without_changing_legacy_fields():
    raw = {
        "numero": "00005/2026",
        "objeto": "Objeto: Servico continuado",
        "situacaoContrato": "Em execucao",
        "valorInicialCompra": 100,
        "valorFinalCompra": 120,
        "dataAssinatura": "2026-01-01",
        "dataInicioVigencia": "2026-01-01",
        "dataFimVigencia": "2099-12-31",
        "unidadeGestora": {
            "codigo": "158157",
            "nome": "INSTITUTO CHICO MENDES - SEDE",
            "orgaoVinculado": {"codigoSIAFI": "44207"},
            "orgaoMaximo": {"nome": "MINISTERIO DO MEIO AMBIENTE"},
        },
    }

    parsed = contratos_portal._parse_contrato(raw)

    assert parsed["numero"] == "00005/2026"
    assert parsed["unidade"] == "INSTITUTO CHICO MENDES - SEDE"
    assert parsed["unidade_codigo"] == "158157"
    assert parsed["orgao_codigo_siafi"] == "44207"
    assert parsed["valor_inicial"] == 100
    assert parsed["valor_final"] == 120


def test_uasg_discovery_prefers_same_portal_contract_then_receipts():
    snapshot = {
        "contratos_detalhe": [
            {"numero": "00100/2025", "unidade_codigo": "111111", "ativo": True},
            {"numero": "00005/2026", "unidade_codigo": "158157", "ativo": True},
        ]
    }

    candidates = contratos_comprasnet._discover_uasg_candidates(
        ["000052026"],
        snapshot,
        [receipt(900_000, "999999")],
    )

    assert [candidate.codigo for candidate in candidates] == ["158157", "999999"]
    assert [candidate.origem for candidate in candidates] == [
        "CONTRATOS_NUMERO",
        "RECEBIDO",
    ]
    assert candidates[0].numero_preferido == "000052026"


def test_uasg_discovery_uses_other_portal_contracts_active_first():
    snapshot = {
        "contratos_detalhe": [
            {"numero": "00001/2020", "unidade_codigo": "222222", "ativo": False},
            {"numero": "00002/2026", "unidade_codigo": "111111", "ativo": True},
        ]
    }

    candidates = contratos_comprasnet._discover_uasg_candidates(
        ["000052026"],
        snapshot,
        [receipt(900_000, "999999")],
    )

    assert [candidate.codigo for candidate in candidates] == [
        "111111",
        "222222",
        "999999",
    ]
    assert [candidate.origem for candidate in candidates] == [
        "CONTRATOS_ATIVO",
        "CONTRATOS_ATIVO",
        "RECEBIDO",
    ]


def test_direct_lookup_rejects_supplier_mismatch_and_tries_next_uasg():
    client = FakeComprasnet(
        {
            "/api/contrato/ugorigem/111111/numeroano/000422026": raw_contract(
                "00.000.000/0001-00", 1
            ),
            "/api/contrato/ugorigem/222222/numeroano/000422026": raw_contract(),
        }
    )

    contract, diagnostics = contratos_comprasnet._find_contract(
        client,
        CNPJ,
        ["000422026"],
        [
            contratos_comprasnet._UasgCandidate("111111", "CONTRATOS_ATIVO"),
            contratos_comprasnet._UasgCandidate("222222", "RECEBIDO"),
        ],
    )

    assert contract["id"] == 42
    assert diagnostics["busca"] == "DIRETA"
    assert diagnostics["fallback_usado"] is False
    assert [item["origem"] for item in diagnostics["tentativas"]] == [
        "CONTRATOS_ATIVO",
        "RECEBIDO",
    ]
    assert len(client.calls) == 2


def test_heavy_fallback_runs_once_only_for_highest_value_uasg():
    client = FakeComprasnet(
        {
            "/api/contrato/ug/111111": [raw_contract()],
        }
    )

    contract, diagnostics = contratos_comprasnet._find_contract(
        client,
        CNPJ,
        ["000422026"],
        [
            contratos_comprasnet._UasgCandidate(
                "111111",
                "CONTRATOS_NUMERO",
                numero_preferido="000422026",
            ),
            contratos_comprasnet._UasgCandidate("222222", "RECEBIDO"),
        ],
    )

    assert contract["id"] == 42
    assert diagnostics["busca"] == "FALLBACK_UG"
    fallback_calls = [path for path in client.calls if "/api/contrato/ug/" in path]
    assert fallback_calls == ["/api/contrato/ug/111111"]


def test_heavy_fallback_is_not_used_for_received_or_unmatched_portal_uasg():
    client = FakeComprasnet({})

    contract, diagnostics = contratos_comprasnet._find_contract(
        client,
        CNPJ,
        ["000422026"],
        [
            contratos_comprasnet._UasgCandidate("111111", "CONTRATOS_ATIVO"),
            contratos_comprasnet._UasgCandidate("222222", "RECEBIDO"),
        ],
    )

    assert contract is None
    assert diagnostics["fallback_usado"] is False
    assert not any("/api/contrato/ug/" in path for path in client.calls)


def test_lookup_failure_is_distinct_from_valid_response_without_match(monkeypatch):
    class FakeBroadfactor:
        def contratos_da_cotacao(self, _cotacao_id):
            return [SimpleNamespace(numero_contrato="000422026")]

        def recebimentos(self, *_args, **_kwargs):
            return [receipt(500_000, "158132")]

    client = FakeComprasnet(
        {
            "/api/contrato/ugorigem/158132/numeroano/000422026": RuntimeError(
                "Comprasnet unavailable"
            ),
            "/api/contrato/ug/158132": RuntimeError("Comprasnet unavailable"),
        }
    )
    monkeypatch.setattr(
        contratos_comprasnet,
        "_load_operation",
        lambda _operation_id: {"cotacao_id": "C-1", "margem_disponivel": 100},
    )
    monkeypatch.setattr(contratos_comprasnet, "_load_contracts_snapshot", lambda _: {})

    result = contratos_comprasnet._fetch(
        _digits_cnpj(CNPJ),
        operation_id="op-1",
        broadfactor_client=FakeBroadfactor(),
        comprasnet_client=client,
    )

    assert result["motivo"] == "falha_consulta_comprasnet"


def test_contract_and_financial_metrics_use_real_api_formats():
    contract = contratos_comprasnet._normalize_contract(raw_contract(), TODAY)
    invoices = [
        {
            "emissao": "2026-01-01",
            "ateste": "2026-01-03",
            "data_liquidacao": "2026-01-05",
            "valor": "100.000,00",
            "glosa": "1.000,00",
            "valorliquido": "99.000,00",
            "situacao": "Siafi Apropriado",
        },
        {
            "emissao": "2026-02-01",
            "ateste": "2026-02-11",
            "data_liquidacao": "2026-03-02",
            "valor": "200.000,00",
            "glosa": "0,00",
            "valorliquido": "200.000,00",
            "situacao": "Pago",
        },
        {
            "emissao": "2026-04-01",
            "ateste": "2026-07-28",
            "data_liquidacao": None,
            "valor": "50.000,00",
            "glosa": "500,00",
            "valorliquido": "49.500,00",
            "situacao": "Pendente",
        },
    ]

    performance = contratos_comprasnet._performance(invoices)
    commitments = contratos_comprasnet._commitments(
        [
            {
                "empenhado": "500.000,00",
                "liquidado": "250.000,00",
                "pago": "200.000,00",
                "aliquidar": "250.000,00",
            }
        ]
    )

    assert contract["valor_global"] == 500_000
    assert contract["prazo_vincendo_meses"] == 6
    assert contract["prorrogavel"] is True
    assert performance["faturado_total"] == 350_000
    assert performance["glosa_total"] == 1_500
    assert performance["taxa_glosa"] == round(1_500 / 350_000, 6)
    assert performance["lag_ateste_mediana"] == 10
    assert performance["lag_ateste_p90"] == 118
    assert performance["lag_ateste_max"] == 118
    assert performance["lag_liquidacao_mediana"] == 10.5
    assert performance["faturas_pendentes"] == 1
    assert performance["valor_pendente"] == 49_500
    assert commitments == {
        "empenhado_total": 500_000,
        "liquidado_total": 250_000,
        "pago_total": 200_000,
        "aliquidar_total": 250_000,
    }


def test_predictability_rhythm_and_margin_consistency():
    low = {"num_parcelas": 1, "valor_parcela": 500_000, "valor_global": 500_000}
    invoices = [
        {"emissao": "2026-01-10", "valor": 100_000},
        {"emissao": "2026-03-10", "valor": 200_000},
    ]

    consistency, flags = contratos_comprasnet._margin_consistency(
        500_000, 200_000, 210_000
    )
    divergent, divergent_flags = contratos_comprasnet._margin_consistency(
        500_000, 200_000, 100_000
    )

    assert contratos_comprasnet._predictability(low) == "BAIXA"
    assert contratos_comprasnet._measurement_rhythm(invoices) == {
        "meses_com_fatura": 2,
        "meses_sem_fatura": 1,
        "valor_medio_mensal": 100_000,
    }
    assert consistency["divergencia_pct"] == 0
    assert flags == ["margem_consistente_com_comprasnet"]
    assert divergent["status"] == "DIVERGENTE"
    assert divergent_flags == ["margem_divergente_com_comprasnet"]


def test_source_precedence_is_comprasnet_then_llm_then_default():
    comprasnet = {
        "contrato_comprasnet": {
            "match_confianca": "CNPJ_CONFERIDO",
            "prazo_vincendo_meses": 5,
            "valor_global": 500_000,
        }
    }
    extraction = {"prazo_vincendo_meses": 8, "valor_global": 480_000}

    primary = contratos_comprasnet._resolved_contract_sources(
        {"prazo_final_meses": 12}, comprasnet, extraction, 12
    )
    llm = contratos_comprasnet._resolved_contract_sources(
        {"prazo_final_meses": 12}, {}, extraction, 12
    )
    default = contratos_comprasnet._resolved_contract_sources(
        {"prazo_final_meses": 12}, {}, {}, 12
    )

    assert primary["prazo_final_meses"] == 5
    assert primary["fonte_prazo_vincendo"] == "COMPRASNET"
    assert primary["valor_global_contrato"] == 500_000
    assert llm["prazo_final_meses"] == 8
    assert llm["fonte_prazo_vincendo"] == "EXTRACAO_LLM"
    assert llm["fonte_valor_global"] == "EXTRACAO_LLM"
    assert default["prazo_final_meses"] == 12
    assert default["prazo_vincendo_indisponivel"] is True
    assert default["fonte_prazo_vincendo"] == "DEFAULT"


def test_source_precedence_is_persisted_in_operation_and_snapshot(monkeypatch):
    updates = []

    class Query:
        def __init__(self, table):
            self.table = table
            self.action = None
            self.payload = None

        def select(self, *_args):
            self.action = "select"
            return self

        def update(self, payload):
            self.action = "update"
            self.payload = payload
            return self

        def eq(self, *_args):
            return self

        def in_(self, *_args):
            return self

        def single(self):
            return self

        def execute(self):
            if self.action == "select" and self.table == "operations":
                return SimpleNamespace(data={"prazo_final_meses": 12})
            if self.action == "select":
                return SimpleNamespace(
                    data=[
                        {
                            "component": "contratos_comprasnet",
                            "status": "completed",
                            "parsed_result": {
                                "status_consulta": "ENCONTRADO",
                                "contrato_comprasnet": {
                                    "match_confianca": "CNPJ_CONFERIDO",
                                    "prazo_vincendo_meses": 5,
                                    "valor_global": 500_000,
                                },
                            },
                        },
                        {
                            "component": "contrato_extracao",
                            "status": "completed",
                            "parsed_result": {
                                "prazo_vincendo_meses": 8,
                                "valor_global": 480_000,
                            },
                        },
                    ]
                )
            updates.append((self.table, self.payload))
            return SimpleNamespace(data=[{"id": "op-1"}])

    fake_supabase = SimpleNamespace(table=lambda name: Query(name))
    monkeypatch.setattr("app.core.database.supabase", fake_supabase)
    monkeypatch.setattr(
        "app.services.eligibility_params_service.get_eligibility_config",
        lambda: {"prazo_padrao_meses": 12},
    )

    resolved = contratos_comprasnet.apply_contract_source_precedence("op-1")

    assert resolved["fonte_prazo_vincendo"] == "COMPRASNET"
    operation_update = next(payload for table, payload in updates if table == "operations")
    snapshot_update = next(
        payload for table, payload in updates if table == "component_snapshots"
    )
    assert operation_update["prazo_final_meses"] == 5
    assert operation_update["valor_global_contrato"] == 500_000
    assert snapshot_update["parsed_result"]["fonte_valor_global"] == "COMPRASNET"


def test_fetch_enriches_match_without_using_heavy_fallback(monkeypatch):
    class FakeBroadfactor:
        def contratos_da_cotacao(self, _cotacao_id):
            return [SimpleNamespace(numero_contrato="000422026")]

        def recebimentos(self, *_args, **_kwargs):
            return [receipt(500_000, "999999")]

    client = FakeComprasnet(
        {
            "/api/contrato/ugorigem/158132/numeroano/000422026": raw_contract(),
            "/api/contrato/42/faturas": [],
            "/api/contrato/42/empenhos": [{"pago": "100.000,00"}],
        }
    )
    monkeypatch.setattr(
        contratos_comprasnet,
        "_load_operation",
        lambda _operation_id: {
            "cotacao_id": "C-1",
            "margem_disponivel": 280_000,
        },
    )
    monkeypatch.setattr(
        contratos_comprasnet,
        "_load_contracts_snapshot",
        lambda _operation_id: {
            "contratos_detalhe": [
                {
                    "numero": "00042/2026",
                    "unidade_codigo": "158132",
                    "ativo": True,
                }
            ]
        },
    )

    result = contratos_comprasnet._fetch(
        _digits_cnpj(CNPJ),
        operation_id="op-1",
        today=TODAY,
        broadfactor_client=FakeBroadfactor(),
        comprasnet_client=client,
    )

    assert result["status_consulta"] == "ENCONTRADO"
    assert result["contrato_comprasnet"]["match_confianca"] == "CNPJ_CONFERIDO"
    assert result["empenhos"]["pago_total"] == 100_000
    assert result["consistencia_margem"]["status"] == "CONSISTENTE"
    assert result["diagnostico_busca"]["tentativas"][0]["origem"] == (
        "CONTRATOS_NUMERO"
    )
    assert not any("/api/contrato/ug/" in path for path in client.calls)


def _digits_cnpj(value):
    return "".join(char for char in value if char.isdigit())


def test_public_api_failure_is_recorded_instead_of_raised(monkeypatch):
    class BrokenBroadfactor:
        def contratos_da_cotacao(self, _cotacao_id):
            raise RuntimeError("Broadfactor unavailable")

    monkeypatch.setattr(
        contratos_comprasnet,
        "_load_operation",
        lambda _operation_id: {"cotacao_id": "C-1", "margem_disponivel": 100},
    )

    result = contratos_comprasnet._fetch(
        _digits_cnpj(CNPJ),
        operation_id="op-1",
        broadfactor_client=BrokenBroadfactor(),
        comprasnet_client=FakeComprasnet({}),
    )

    assert result["status_consulta"] == "NAO_ENCONTRADO"
    assert result["motivo"] == "falha_consulta_comprasnet"
    assert "Broadfactor unavailable" in result["error"]
