"""Characterization tests for the current deterministic sanction gates."""
from app.workers.tasks.score_engine import (
    _fontes_sancao_nao_verificadas,
    gates_deterministicos,
)


def test_cadastro_gate_uses_brasil_api_then_pessoa_juridica_and_ignores_absence():
    assert gates_deterministicos({"brasil_api": {"situacao_cadastral": "BAIXADA"}}) == [
        "Situacao cadastral diferente de ATIVA: BAIXADA"
    ]
    assert gates_deterministicos({"pessoa_juridica": {"situacao": "SUSPENSA"}}) == [
        "Situacao cadastral diferente de ATIVA: SUSPENSA"
    ]
    assert gates_deterministicos({"brasil_api": {"situacao_cadastral": None}}) == []
    assert gates_deterministicos({}) == []


def test_pessoa_juridica_sanction_flags_all_create_the_same_gate():
    for field in ("possui_sancao", "sancionado_ceis", "sancionado_cnep", "sancionado_cepim", "sancionado_ceaf"):
        assert gates_deterministicos({"pessoa_juridica": {field: True}}) == [
            "Sancao ativa identificada em bases restritivas"
        ]


def test_sanction_components_characterize_active_inactive_empty_and_absent_records():
    for component in ("ceis", "cnep", "cepim", "ceaf"):
        assert gates_deterministicos({component: {"total_registros": 1, "registros": [{"situacao": "ATIVO"}]}}) == [
            f"Sancao ativa em {component.upper()}"
        ]
        assert gates_deterministicos({component: {"total_registros": 1, "registros": [{"situacao": "ENCERRADO"}]}}) == []
        # Current behavior treats a declared count with no detail list as active.
        assert gates_deterministicos({component: {"total_registros": 1, "registros": []}}) == [
            f"Sancao ativa em {component.upper()}"
        ]
        assert gates_deterministicos({}) == []


def test_acordos_characterize_active_inactive_and_empty_records():
    active = {"acordos_leniencia": {"total_acordos": 1, "acordos": [{"situacao": "VIGENTE"}]}}
    inactive = {"acordos_leniencia": {"total_acordos": 1, "acordos": [{"situacao": "ENCERRADO"}]}}
    empty = {"acordos_leniencia": {"total_acordos": 1, "acordos": []}}
    assert gates_deterministicos(active) == ["Acordo de leniencia ativo"]
    assert gates_deterministicos(inactive) == []
    assert gates_deterministicos(empty) == ["Acordo de leniencia ativo"]


def test_gates_are_deduplicated_and_sorted():
    result = gates_deterministicos({
        "brasil_api": {"situacao_cadastral": "INATIVA"},
        "pessoa_juridica": {"possui_sancao": True},
        "ceis": {"total_registros": 1, "registros": []},
    })
    assert result == sorted(result)
    assert len(result) == len(set(result))


def test_unverified_sources_only_include_missing_components_and_pessoa_error():
    assert _fontes_sancao_nao_verificadas({}) == [
        "acordos_leniencia", "ceis", "cepim", "cnep"
    ]
    assert _fontes_sancao_nao_verificadas({
        "ceis": {}, "cnep": {}, "cepim": {}, "acordos_leniencia": {},
        "pessoa_juridica": {"erro": "sem_registro"},
    }) == ["pessoa_juridica"]
