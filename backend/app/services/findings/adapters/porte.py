"""Source: score_engine.score_porte_llm (1239), persisted by score_engine."""
from __future__ import annotations

from app.services.findings.adapters.common import finding, unverified
from app.services.findings.schemas import Confianca, Escopo


def emit_porte(snapshots, *, fingerprint: str, operation=None):
    engine = snapshots.get("score_engine")
    porte = ((engine or {}).get("dimensoes") or {}).get("porte_operacionalidade") if isinstance(engine, dict) else None
    if not isinstance(porte, dict) or not porte.get("nivel"):
        return [unverified("capacidade_operacional", Escopo.CEDENTE, component="score_engine", path="dimensoes.porte_operacionalidade", fingerprint=fingerprint)]
    return [finding("capacidade_operacional", Escopo.CEDENTE, str(porte["nivel"]), component="score_engine", path="dimensoes.porte_operacionalidade.nivel", fingerprint=fingerprint, confidence=Confianca.MEDIA)]
