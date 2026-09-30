from app.services.findings.adapters.cadastro_regularidade import emit_cadastro_regularidade


def test_cadastro_adapter_emits_official_gate_and_balance_catalog():
    findings = emit_cadastro_regularidade({
        "brasil_api": {"situacao_cadastral": "BAIXADA", "capital_social": 1000, "porte": "ME"},
        "pessoa_juridica": {}, "acordos_leniencia": {"possui_acordo": False},
        "catalogo_broadfactor": {"documentos_broadfactor": [{"tipo": "DRE"}]},
    }, fingerprint="x")
    by_code = {item.codigo: item for item in findings}
    assert by_code["cadastro_inativo"].valor is True
    assert by_code["balanco_ausente"].valor is False
    assert by_code["balanco_catalogado_broadfactor"].valor["tipos"] == ["DRE"]
