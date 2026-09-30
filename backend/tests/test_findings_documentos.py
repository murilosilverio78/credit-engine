from app.services.findings.adapters.documentos import emit_documentos
from app.services.findings.schemas import Estado


def test_document_adapter_limits_llm_confidence_and_requires_complete_read_for_negative():
    complete = emit_documentos({"contrato_extracao": {"regime_conta_vinculada": "NAO_IDENTIFICADO", "flags": []}}, fingerprint="x")[0]
    truncated = emit_documentos({"contrato_extracao": {"regime_conta_vinculada": "NAO_IDENTIFICADO", "flags": ["documento_truncado_para_ocr"]}}, fingerprint="x")[0]
    assert complete.estado == Estado.NEGATIVO_CONFIRMADO
    assert truncated.estado == Estado.NAO_VERIFICADO
    assert complete.confianca == "MEDIA"
