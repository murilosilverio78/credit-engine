from datetime import date

from app.services.policy import shadow
from app.services.policy.engine import _limit
from app.services.policy.types import EntradaPolitica, FindingValue
from tests.fakes.postgrest import Postgrest


class _InsertQuery:
    def __init__(self, sink):
        self.sink = sink

    def insert(self, payload):
        self.sink.append(payload)
        return self

    def upsert(self, payload, *, on_conflict, ignore_duplicates):
        keys = [key.strip() for key in on_conflict.split(",")]
        if not any(all(item.get(key) == payload.get(key) for key in keys) for item in self.sink):
            self.sink.append(payload)
        return self

    def execute(self):
        return type("Response", (), {"data": []})()


class _Db:
    def __init__(self):
        self.inserted = []

    def table(self, _name):
        return _InsertQuery(self.inserted)


def _complete_parity_runs():
    return {
        name: {"run_id": name, "status": "COMPLETO"}
        for name in ("cadastro_regularidade", "sacado_orgao", "reputacional", "porte")
    }


def _shadow_result(values, *, limite_multiplicadores=None):
    return type("Result", (), {
        "as_dict": lambda self: values,
        "efeitos_novos": [],
        "limite_multiplicadores": limite_multiplicadores or [],
    })()


def _rule(code, classe, target=None):
    return {"codigo": code, "classe": classe, "parametro_alvo": target}


def test_comparative_shadow_classifies_fired_new_veto_cp_and_authority_rules():
    cases = [
        (
            {"vetos": {"versao": ["antecipacao"], "comparar_com": []}},
            [_rule("antecipacao", "VETO")],
            {"bloqueios": ["antecipacao"]},
        ),
        (
            {"condicoes_precedentes": {"versao": [{"codigo": "empenho"}], "comparar_com": []}},
            [_rule("empenho", "CONDICAO", "cp")],
            {"condicoes_precedentes": [{"codigo": "empenho"}]},
        ),
        (
            {"alcada_minima": {"versao": 2, "comparar_com": 1}},
            [_rule("cobertura", "AJUSTE", "alcada")],
            {"alcada_motivos": [{"codigo": "cobertura"}]},
        ),
    ]

    for diff, primary_rules, result in cases:
        classe, rules = shadow._comparative_rule_class(diff, primary_rules, [], result)
        assert classe == "ESPERADA"
        assert rules == [{**primary_rules[0], "disparou": True}]


def test_comparative_shadow_marks_unexplained_limit_and_empty_diff():
    shared_rule = _rule("rating", "AJUSTE", "limite_mult")
    unexpected, rules = shadow._comparative_rule_class(
        {"limite": {"versao": 85, "comparar_com": 100}},
        [shared_rule], [shared_rule],
        {"limite_multiplicadores": [{"codigo": "rating"}]},
    )
    equal, _rules = shadow._comparative_rule_class({}, [shared_rule], [], {})

    assert unexpected == "INESPERADA"
    assert rules == []
    assert equal == "IGUAL"


def test_comparar_sombra_persists_comparative_only_on_primary_run(monkeypatch):
    primary_rule = _rule("empenho", "CONDICAO", "cp")
    monkeypatch.setattr(
        shadow,
        "load_policy",
        lambda *, versao, **_kwargs: {"version": {"id": f"p{versao}", "versao": versao}, "regras": [primary_rule] if versao == 3 else []},
    )

    def fake_evaluate(_operation_id, *, versao, **_kwargs):
        return {
            "operation_id": "op", "policy_version_id": f"p{versao}", "policy_version": {"versao": versao},
            "entrada_hash": f"hash-{versao}",
            "resultado": {
                "rating": "C", "limite_aprovado_rs": 100, "bloqueios": [],
                "condicoes_precedentes": [{"codigo": "empenho"}] if versao == 3 else [],
                "alcada_minima": 1, "taxa_ajuste_pp": 0,
            },
        }

    monkeypatch.setattr(shadow, "avaliar_sombra", fake_evaluate)
    db = _Db()

    compared = shadow.comparar_sombra("op", versao=3, comparar_com=2, database=db, aplicar=True)

    assert len(db.inserted) == 1
    persisted = db.inserted[0]
    assert persisted["policy_version_id"] == "p3"
    assert persisted["classe_comparativo"] == "ESPERADA"
    assert persisted["comparativo"] == {
        "comparar_com": 2,
        "diff": {"condicoes_precedentes": {
            "versao": [{"codigo": "empenho"}], "comparar_com": [],
        }},
        "regras_novas": [{"codigo": "empenho", "classe": "CONDICAO", "parametro_alvo": "cp", "disparou": True}],
    }
    assert compared["shadow"]["comparativo"] == persisted["comparativo"]


def test_shadow_classifies_missing_runs_and_dry_run_does_not_persist(monkeypatch):
    params = {"score_bloqueio": 20}
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": params, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, {}, {"cadastro_regularidade"}))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {"as_dict": lambda self: {field: None for field in shadow._FIELDS}, "efeitos_novos": []})())
    db = _Db()
    result = shadow.avaliar_sombra("op", database=db, aplicar=False)
    assert result["classe_geral"] == "SEM_DADOS"
    assert db.inserted == []
    shadow.avaliar_sombra("op", database=db, aplicar=True)
    assert len(db.inserted) == 1
    shadow.avaliar_sombra("op", database=db, aplicar=True)
    assert len(db.inserted) == 1


def test_shadow_hash_includes_canonical_policy_content(monkeypatch):
    policy = {
        "version": {"id": "policy"},
        "parametros": {"peso": 0.4},
        "regras": [
            {"id": "rule-2", "ordem": 2, "magnitude": 1.0},
            {"id": "rule-1", "ordem": 1, "magnitude": 1.1},
        ],
    }
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: policy)
    monkeypatch.setattr(
        shadow,
        "montar_entrada",
        lambda *_args, **_kwargs: EntradaPolitica({}, {}, {}, {"cadastro_regularidade"}),
    )
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({}, date(2026, 1, 1)))
    monkeypatch.setattr(
        shadow,
        "avaliar",
        lambda *_args: _shadow_result({field: None for field in shadow._FIELDS}),
    )
    db = _Db()

    first = shadow.avaliar_sombra("op", database=db, aplicar=True)
    policy["regras"] = list(reversed(policy["regras"]))
    identical_content = shadow.avaliar_sombra("op", database=db, aplicar=True)

    assert first["entrada_hash"] == identical_content["entrada_hash"]
    assert len(db.inserted) == 1

    policy["regras"][1]["magnitude"] = 1.2
    changed_content = shadow.avaliar_sombra("op", database=db, aplicar=True)

    assert changed_content["entrada_hash"] != first["entrada_hash"]
    assert len(db.inserted) == 2


def test_shadow_hash_includes_logic_version(monkeypatch):
    policy = {"version": {"id": "policy"}, "parametros": {}, "regras": []}
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: policy)
    monkeypatch.setattr(
        shadow,
        "montar_entrada",
        lambda *_args, **_kwargs: EntradaPolitica({}, {}, {}, {"cadastro_regularidade"}),
    )
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "_set_pct_max_contrato", lambda *_args: None)
    monkeypatch.setattr(
        shadow,
        "avaliar",
        lambda *_args: _shadow_result({field: None for field in shadow._FIELDS}),
    )
    db = _Db()

    first = shadow.avaliar_sombra("op", database=db, aplicar=True)
    monkeypatch.setattr(shadow, "SHADOW_LOGIC_VERSION", "5")
    changed_logic = shadow.avaliar_sombra("op", database=db, aplicar=True)

    assert first["entrada_hash"] != changed_logic["entrada_hash"]
    assert first["resultado"]["shadow_logic_version"] == "4"
    assert changed_logic["resultado"]["shadow_logic_version"] == "5"
    assert len(db.inserted) == 2


def test_shadow_does_not_require_documentos_run_for_score_parity(monkeypatch):
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    runs = {name: {"run_id": name, "status": "COMPLETO"} for name in (
        "cadastro_regularidade", "sacado_orgao", "reputacional", "porte",
    )}
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, runs, {"documentos"}))
    official = {field: None for field in shadow._FIELDS}
    monkeypatch.setattr(shadow, "_official", lambda *_args: (official, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {
        "as_dict": lambda self: {field: None for field in shadow._FIELDS}, "efeitos_novos": [],
    })())

    assert shadow.avaliar_sombra("op", database=_Db())["classe_geral"] == "IGUAL"


def test_shadow_requires_reputacional_when_official_score_exists(monkeypatch):
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    runs = {name: {"run_id": name, "status": "COMPLETO"} for name in (
        "cadastro_regularidade", "sacado_orgao", "porte",
    )}
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, runs, {"documentos", "reputacional"}))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({"score": 70}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {
        "as_dict": lambda self: {field: (70 if field == "score" else None) for field in shadow._FIELDS}, "efeitos_novos": [],
    })())

    assert shadow.avaliar_sombra("op", database=_Db())["classe_geral"] == "SEM_DADOS"


def test_official_uses_score_completion_date_before_operation_creation():
    db = Postgrest({"component_snapshots": [{
        "operation_id": "op", "component": "score_engine", "status": "completed",
        "created_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-06T00:00:00Z",
        "parsed_result": {"score": 55},
    }]})

    official, reference = shadow._official(db, "op")

    assert official == {"score": 55}
    assert reference == date(2026, 1, 6)
    # This is deliberately past the strict one-year boundary: using the
    # operation creation date (Jan 1) would select the lower age band.
    opened = date(2025, 1, 5)
    assert (reference - opened).days / 365.25 > 1


def test_official_uses_creation_date_only_when_completion_is_null():
    db = Postgrest({"component_snapshots": [{
        "operation_id": "op", "component": "score_engine", "status": "completed",
        "created_at": "2026-01-01T00:00:00Z", "completed_at": None,
        "parsed_result": {"score": 55},
    }]})

    _official, reference = shadow._official(db, "op")

    assert reference == date(2026, 1, 1)


def test_official_reads_fator_potencial_from_real_regularidade_shape():
    db = Postgrest({"component_snapshots": [{
        "operation_id": "op", "component": "score_engine", "status": "completed",
        "created_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-02T00:00:00Z",
        "parsed_result": {"regularidade": {"fator_potencial": 0.91}},
    }]})

    official, _reference_date = shadow._official(db, "op")

    assert official["fator_potencial"] == 0.91


def test_date_accepts_iso_and_brazilian_certificate_validity_formats():
    assert shadow._date("2026-09-16") == date(2026, 9, 16)
    assert shadow._date("16/09/2026") == date(2026, 9, 16)


def test_shadow_uses_eligibility_cap_when_no_official_score(monkeypatch):
    import app.services.eligibility_params_service as eligibility
    monkeypatch.setattr(eligibility, "get_eligibility_config", lambda: {"pct_max_contrato": 0.35})
    operation = {}

    shadow._set_pct_max_contrato(operation, None)

    assert operation == {"pct_max_contrato": 0.35, "pct_max_contrato_origem": "eligibility"}


def test_shadow_preserves_official_cap_after_eligibility_changes(monkeypatch):
    import app.services.eligibility_params_service as eligibility
    monkeypatch.setattr(eligibility, "get_eligibility_config", lambda: {"pct_max_contrato": 0.10})
    operation = {}

    shadow._set_pct_max_contrato(operation, {"limite_sugerido_pct_contrato": 0.42})

    assert operation == {"pct_max_contrato": 0.42, "pct_max_contrato_origem": "oficial"}


def test_shadow_missing_eligibility_cap_keeps_limit_flag(monkeypatch):
    import app.services.eligibility_params_service as eligibility
    monkeypatch.setattr(eligibility, "get_eligibility_config", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    operation = {"contrato_saldo": 500_000}

    shadow._set_pct_max_contrato(operation, None)
    _value, flags = _limit(EntradaPolitica(operation, {}), {"pct_margem_sobre_saldo": 0.7}, {})

    assert operation["pct_max_contrato_origem"] == "indisponivel"
    assert flags == ["limite_sem_pct_max_contrato"]


def test_shadow_compares_veto_blocks_by_category(monkeypatch):
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, {}, set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({"bloqueios": ["Situacao cadastral diferente de ATIVA: BAIXADA"]}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {
        "as_dict": lambda self: {field: (["cadastro_inativo"] if field == "bloqueios" else None) for field in shadow._FIELDS},
        "efeitos_novos": [],
    })())

    payload = shadow.avaliar_sombra("op", database=_Db())

    assert not [item for item in payload["divergencias"] if item["campo"] == "bloqueios"]


def test_shadow_effects_without_parity_field_do_not_explain_divergence(monkeypatch):
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    runs = {name: {"run_id": name, "status": "COMPLETO"} for name in (
        "cadastro_regularidade", "sacado_orgao", "reputacional", "porte",
    )}
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, runs, set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({"score": 70}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {
        "as_dict": lambda self: {field: (60 if field == "score" else None) for field in shadow._FIELDS},
        "efeitos_novos": [{"codigo": "glosa_historica", "parametro_alvo": "pd_multiplicador"}],
    })())

    assert shadow.avaliar_sombra("op", database=_Db())["classe_geral"] == "INESPERADA"


def test_shadow_classifies_certificate_expired_after_official_as_temporal_input(monkeypatch):
    values = {field: None for field in shadow._FIELDS}
    values.update({
        "fator_regularidade": 0.91,
        "score": 65,
        "bloqueios": [],
    })
    official = {**values, "fator_regularidade": 0.96, "score": 70}
    findings = {
        "certidao_cnd_federal_pendente": FindingValue(
            "certidao_cnd_federal_pendente",
            {"estado": "ausente", "validade": "16/09/2026"},
            "CONFIRMADO",
            "ALTA",
        )
    }
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, findings, _complete_parity_runs(), set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: (official, date(2026, 9, 10)))
    monkeypatch.setattr(shadow, "_today", lambda: date(2026, 9, 16))
    monkeypatch.setattr(shadow, "_set_pct_max_contrato", lambda *_args: None)
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: _shadow_result(values))

    payload = shadow.avaliar_sombra("op", database=_Db())

    assert payload["classe_geral"] == "INSUMO_TEMPORAL"
    assert {item["classe"] for item in payload["divergencias"]} == {"INSUMO_TEMPORAL"}
    assert {item["campo"] for item in payload["divergencias"]} == {
        "fator_regularidade",
        "score",
    }


def test_temporal_certificate_must_already_be_expired(monkeypatch):
    entrada = EntradaPolitica(
        {},
        {
            "certidao_fgts_pendente": FindingValue(
                "certidao_fgts_pendente",
                {"estado": "ausente", "validade": "16/09/2026"},
                "CONFIRMADO",
                "ALTA",
            )
        },
    )
    monkeypatch.setattr(shadow, "_today", lambda: date(2026, 9, 15))

    assert not shadow._is_temporal_certificate_input(
        entrada,
        {"fator_regularidade": 0.96},
        {"fator_regularidade": 0.91},
        date(2026, 9, 10),
        [{"campo": "fator_regularidade"}],
    )


def test_shadow_effect_with_explicit_parity_field_explains_divergence(monkeypatch):
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    runs = {name: {"run_id": name, "status": "COMPLETO"} for name in (
        "cadastro_regularidade", "sacado_orgao", "reputacional", "porte",
    )}
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, runs, set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({"score": 70}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {
        "as_dict": lambda self: {field: (60 if field == "score" else None) for field in shadow._FIELDS},
        "efeitos_novos": [{"codigo": "efeito_futuro", "campos_paridade": ["score"]}],
    })())

    assert shadow.avaliar_sombra("op", database=_Db())["classe_geral"] == "ESPERADA"


def test_shadow_marks_limit_multiplier_projection_as_expected(monkeypatch):
    values = {field: None for field in shadow._FIELDS}
    values.update({"bloqueios": [], "limite_aprovado_rs": 385_000})
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, _complete_parity_runs(), set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({"limite_aprovado_rs": 550_000}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "_set_pct_max_contrato", lambda *_args: None)
    monkeypatch.setattr(
        shadow,
        "avaliar",
        lambda *_args: _shadow_result(
            values,
            limite_multiplicadores=[{"codigo": "rating_d", "fator": 0.7}],
        ),
    )

    assert shadow.avaliar_sombra("op", database=_Db())["classe_geral"] == "ESPERADA"


def test_shadow_partial_run_is_sem_dados(monkeypatch):
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, {"sacado_orgao": {"run_id": "run", "status": "PARCIAL"}}, set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({"score": 70}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {
        "as_dict": lambda self: {field: (60 if field == "score" else None) for field in shadow._FIELDS},
        "efeitos_novos": [],
    })())

    assert shadow.avaliar_sombra("op", database=_Db())["classe_geral"] == "SEM_DADOS"


def test_shadow_marks_absent_legacy_official_fields_as_non_comparable(monkeypatch):
    values = {field: None for field in shadow._FIELDS}
    values.update({"score": 70, "rating": "C", "bloqueios": [], "fator_regularidade": 1.0})
    official = {key: value for key, value in values.items() if key not in {
        "merit_potencial", "rating_potencial", "penalizacao_balanco", "ajuste_pd", "fator_potencial",
    }}
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, _complete_parity_runs(), set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: (official, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "_set_pct_max_contrato", lambda *_args: None)
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: _shadow_result(values))

    payload = shadow.avaliar_sombra("op", database=_Db())

    assert payload["classe_geral"] == "IGUAL"
    assert {item["campo"] for item in payload["divergencias"]} == {
        "merit_potencial", "rating_potencial", "penalizacao_balanco", "ajuste_pd", "fator_potencial",
    }
    assert {item["classe"] for item in payload["divergencias"]} == {"NAO_COMPARAVEL"}


def test_shadow_marks_old_pd_shape_as_non_comparable(monkeypatch):
    adjustment = {
        "faixa_volatilidade": "BAIXA", "multiplicador_volatilidade": 1.0,
        "pd_base": 0.032, "pd_ajustada": 0.032,
        "min_anos_completos": 2, "anos_completos": 10,
    }
    values = {field: None for field in shadow._FIELDS}
    values.update({"score": 70, "rating": "C", "bloqueios": [], "fator_regularidade": 1.0, "fator_potencial": 1.0, "ajuste_pd": adjustment})
    official = dict(values)
    official["ajuste_pd"] = {key: value for key, value in adjustment.items() if key not in {"min_anos_completos", "anos_completos"}}
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, _complete_parity_runs(), set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: (official, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "_set_pct_max_contrato", lambda *_args: None)
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: _shadow_result(values))

    payload = shadow.avaliar_sombra("op", database=_Db())

    assert payload["classe_geral"] == "IGUAL"
    assert payload["divergencias"] == [{
        "campo": "ajuste_pd", "valor_politica": adjustment,
        "valor_oficial": official["ajuste_pd"], "diferenca": None,
        "classe": "NAO_COMPARAVEL",
        "motivo": "formato antigo de ajuste_pd sem anos completos",
    }]


def test_shadow_compares_only_current_pd_projection(monkeypatch):
    adjustment = {
        "faixa_volatilidade": "BAIXA", "multiplicador_volatilidade": 1.0,
        "pd_base": 0.032, "pd_ajustada": 0.032,
        "min_anos_completos": 2, "anos_completos": 10, "parametro": "novo",
    }
    values = {field: None for field in shadow._FIELDS}
    values.update({"score": 70, "rating": "C", "bloqueios": [], "fator_regularidade": 1.0, "fator_potencial": 1.0, "ajuste_pd": adjustment})
    official = dict(values)
    official["ajuste_pd"] = {**adjustment, "parametro": "historico"}
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, _complete_parity_runs(), set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: (official, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "_set_pct_max_contrato", lambda *_args: None)
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: _shadow_result(values))

    assert shadow.avaliar_sombra("op", database=_Db())["divergencias"] == []


def test_input_components_after_score_ignores_score_and_older_snapshots():
    db = Postgrest({"component_snapshots": [
        {"operation_id": "op", "component": "score_engine", "status": "completed", "completed_at": "2026-09-23T01:00:00+00:00"},
        {"operation_id": "op", "component": "contratos", "status": "completed", "completed_at": "2026-10-01T01:00:00+00:00"},
        {"operation_id": "op", "component": "brasil_api", "status": "completed", "completed_at": "2026-09-22T01:00:00+00:00"},
        {"operation_id": "op", "component": "score_engine", "status": "completed", "completed_at": "2026-10-02T01:00:00+00:00"},
        {"operation_id": "op", "component": "desconhecido", "status": "completed", "completed_at": "2026-10-03T01:00:00+00:00"},
    ]})

    assert shadow._input_components_after_score(db, "op") == []


def test_input_components_after_score_lists_newer_recognized_inputs():
    db = Postgrest({"component_snapshots": [
        {"operation_id": "op", "component": "score_engine", "status": "completed", "completed_at": "2026-09-23T01:00:00+00:00"},
        {"operation_id": "op", "component": "contratos", "status": "completed", "completed_at": "2026-10-01T01:00:00+00:00"},
        {"operation_id": "op", "component": "brasil_api", "status": "completed", "completed_at": "2026-10-01T01:00:00+00:00"},
        {"operation_id": "op", "component": "score_engine", "status": "completed", "completed_at": "2026-09-23T01:00:00+00:00"},
    ]})

    assert shadow._input_components_after_score(db, "op") == ["brasil_api", "contratos"]


def test_shadow_marks_parity_divergence_as_later_input(monkeypatch):
    values = {field: None for field in shadow._FIELDS}
    values.update({"score": 60, "bloqueios": []})
    official = {**values, "score": 70}
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, _complete_parity_runs(), set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: (official, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "_input_components_after_score", lambda *_args: ["contratos"])
    monkeypatch.setattr(shadow, "_set_pct_max_contrato", lambda *_args: None)
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: _shadow_result(values))

    payload = shadow.avaliar_sombra("op", database=_Db())

    assert payload["classe_geral"] == "SEM_DADOS"
    assert payload["oficial"]["_insumos_posteriores"] == ["contratos"]
    assert payload["divergencias"] == [{
        "campo": "score", "valor_politica": 60, "valor_oficial": 70,
        "diferenca": None, "classe": "INSUMO_POSTERIOR",
        "motivo": "insumos refeitos apos o score oficial",
        "componentes_posteriores": ["contratos"],
    }]


def test_shadow_keeps_equal_when_later_inputs_reproduce_official(monkeypatch):
    values = {field: None for field in shadow._FIELDS}
    values.update({"score": 60, "bloqueios": []})
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, _complete_parity_runs(), set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: (dict(values), date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "_input_components_after_score", lambda *_args: ["contratos"])
    monkeypatch.setattr(shadow, "_set_pct_max_contrato", lambda *_args: None)
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: _shadow_result(values))

    payload = shadow.avaliar_sombra("op", database=_Db())

    assert payload["classe_geral"] == "IGUAL"
    assert payload["divergencias"] == []
    assert payload["oficial"]["_insumos_posteriores"] == ["contratos"]
