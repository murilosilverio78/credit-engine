from app.services.findings.adapters.reputacional import emit_reputacional


def test_reputational_adapter_marks_non_isolated_research_low_confidence():
    findings = emit_reputacional({"web_research": {"nivel": "Forte", "alertas": ["alerta"], "flags_reputacao": ["reputacao_nao_isolada"]}}, fingerprint="x")
    assert {item.codigo for item in findings} == {"reputacao_mercado", "alertas_reputacionais"}
    assert all(item.confianca == "BAIXA" for item in findings)
