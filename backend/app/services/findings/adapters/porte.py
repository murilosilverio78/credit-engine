"""Source: score_engine.score_porte_llm, persisted by score_engine.

Emitter v5 selects ``nivel_potencial`` so the policy applies the balance
penalty exactly once; runs from older versions must be re-emitted.
"""
from __future__ import annotations

from app.services.findings.adapters.common import finding, unverified
from app.services.findings.schemas import Confianca, Escopo


def emit_porte(snapshots, *, fingerprint: str, operation=None):
    engine = snapshots.get("score_engine")
    porte = ((engine or {}).get("dimensoes") or {}).get("porte_operacionalidade") if isinstance(engine, dict) else None
    if not isinstance(porte, dict):
        return [unverified("capacidade_operacional", Escopo.CEDENTE, component="score_engine", path="dimensoes.porte_operacionalidade", fingerprint=fingerprint)]
    effective, potential = porte.get("nivel"), porte.get("nivel_potencial")
    used = potential if potential is not None else effective
    if not used:
        return [unverified("capacidade_operacional", Escopo.CEDENTE, component="score_engine", path="dimensoes.porte_operacionalidade", fingerprint=fingerprint)]
    path = "dimensoes.porte_operacionalidade.nivel_potencial" if potential is not None else "dimensoes.porte_operacionalidade.nivel"
    result = finding("capacidade_operacional", Escopo.CEDENTE, str(used), component="score_engine", path=path, fingerprint=fingerprint, confidence=Confianca.MEDIA)
    result.evidencia[0].update({"nivel_efetivo": effective, "nivel_potencial": potential, "nivel_usado": "nivel_potencial" if potential is not None else "nivel"})
    return [result]
