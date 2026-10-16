"""Contratos puros do extrator financeiro; nao usam documentos reais."""
from app.services.documentos_financeiros.service import _consolidate, _manifest, _safe_payload


def test_payload_financeiro_remove_chaves_interpretadas_pelo_score_e_cpf():
    payload = _safe_payload({
        "familia": "IRPF",
        "tipo": "IRPF",
        "nested": {"document_type": "x", "cpf": "123.456.789-09", "cpf_final": "12345678909"},
        "trecho": "CPF 123.456.789-09",
    })
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
    result = _consolidate([{"sha256": "a" * 64, "familia": "IRPF", "status": "EXTRAIDO", "payload": {"familia": "IRPF"}}], "m")

    def visit(value):
        if isinstance(value, dict):
            assert not {"tipo", "tipo_documento", "document_type"}.intersection(value)
            for item in value.values(): visit(item)
        if isinstance(value, list):
            for item in value: visit(item)
    visit(result)
