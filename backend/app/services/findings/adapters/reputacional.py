"""Source: score_engine.score_reputacao (1051), backed by web_research."""
from __future__ import annotations

from app.services.findings.adapters.common import finding, unverified
from app.services.findings.schemas import Confianca, Escopo


def emit_reputacional(snapshots, *, fingerprint: str, operation=None):
    web = snapshots.get("web_research")
    if not isinstance(web, dict):
        return [unverified("reputacao_mercado", Escopo.CEDENTE, component="web_research", path="nivel", fingerprint=fingerprint), unverified("alertas_reputacionais", Escopo.CEDENTE, component="web_research", path="alertas", fingerprint=fingerprint)]
    from app.workers.tasks.score_engine import score_reputacao
    effective = score_reputacao({"web_research": web})
    confidence = Confianca.BAIXA if "reputacao_nao_isolada" in (web.get("flags_reputacao") or []) else Confianca.MEDIA
    level = finding("reputacao_mercado", Escopo.CEDENTE, str(effective.get("nivel") or "Adequado"), component="web_research", path="nivel", fingerprint=fingerprint, confidence=confidence)
    level.evidencia[0]["motivo"] = list(effective.get("flags") or [])
    return [
        level,
        finding("alertas_reputacionais", Escopo.CEDENTE, {"alertas": web.get("alertas") or [], "flags": web.get("flags_reputacao") or []}, component="web_research", path="alertas", fingerprint=fingerprint, confidence=confidence),
    ]
