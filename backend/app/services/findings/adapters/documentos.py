"""Source: contrato_extracao._normalize_extraction (405)."""
from __future__ import annotations

from app.services.findings.adapters.common import finding, unverified
from app.services.findings.schemas import Confianca, Escopo, Estado


def emit_documentos(snapshots, *, fingerprint: str, operation=None):
    extraction = snapshots.get("contrato_extracao")
    if not isinstance(extraction, dict):
        return [unverified("conta_vinculada_regime", Escopo.CONTRATO, component="contrato_extracao", path="regime_conta_vinculada", fingerprint=fingerprint)]
    regime = extraction.get("regime_conta_vinculada")
    truncated = bool("texto_truncado_para_llm" in (extraction.get("flags") or []) or "documento_truncado_para_ocr" in (extraction.get("flags") or []))
    if not regime:
        return [unverified("conta_vinculada_regime", Escopo.CONTRATO, component="contrato_extracao", path="regime_conta_vinculada", fingerprint=fingerprint)]
    state = Estado.CONFIRMADO if regime != "NAO_IDENTIFICADO" else (Estado.NAO_VERIFICADO if truncated else Estado.NEGATIVO_CONFIRMADO)
    return [finding("conta_vinculada_regime", Escopo.CONTRATO, str(regime), component="contrato_extracao", path="regime_conta_vinculada", fingerprint=fingerprint, state=state, confidence=Confianca.MEDIA)]
