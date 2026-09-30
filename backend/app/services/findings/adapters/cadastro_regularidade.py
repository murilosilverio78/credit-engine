"""Sources: score_engine.gates_deterministicos (1177), score_regularidade
(1116), _apply_missing_balance_penalty (621), score_saude_cadastral (815)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.document_types import BALANCO_DOCUMENT_TYPES
from app.services.findings.adapters.common import finding, unverified
from app.services.findings.schemas import Confianca, Escopo, Estado


def _types(value: Any) -> set[str]:
    if isinstance(value, list):
        return set().union(*(_types(item) for item in value)) if value else set()
    if not isinstance(value, dict):
        return set()
    found = {str(item).strip().upper() for key, item in value.items()
             if str(key).lower() in {"tipo", "tipo_documento", "document_type"} and isinstance(item, str)}
    for item in value.values():
        if isinstance(item, (dict, list)):
            found |= _types(item)
    return found


def emit_cadastro_regularidade(snapshots: dict[str, Any], *, fingerprint: str, operation: dict[str, Any] | None = None):
    result = []
    brasil = snapshots.get("brasil_api") or {}
    pessoa = snapshots.get("pessoa_juridica") or {}
    situacao = brasil.get("situacao_cadastral") or pessoa.get("situacao_cadastral") or pessoa.get("situacao")
    if situacao is None:
        result.append(unverified("cadastro_inativo", Escopo.CEDENTE, component="brasil_api", path="situacao_cadastral", fingerprint=fingerprint))
    else:
        active = str(situacao).strip().upper() == "ATIVA"
        result.append(finding("cadastro_inativo", Escopo.CEDENTE, not active, component="brasil_api" if brasil else "pessoa_juridica", path="situacao_cadastral", fingerprint=fingerprint, state=Estado.NEGATIVO_CONFIRMADO if active else Estado.CONFIRMADO))
    sanction_components = ("ceis", "cnep", "cepim", "ceaf")
    sanction_details = []
    for component in sanction_components:
        item = snapshots.get(component)
        if isinstance(item, dict) and (item.get("possui_sancao") or item.get("total_registros")):
            sanction_details.append({"componente": component, "registros": item.get("registros") or []})
    if pessoa.get("possui_sancao") or any(pessoa.get(f"sancionado_{name}") for name in ("ceis", "cnep", "cepim", "ceaf")):
        sanction_details.append({"componente": "pessoa_juridica", "flags": True})
    result.append(finding("sancao_ativa", Escopo.CEDENTE, sanction_details, component="pessoa_juridica", path="flags_sancao", fingerprint=fingerprint, state=Estado.CONFIRMADO if sanction_details else Estado.NEGATIVO_CONFIRMADO))
    acordos = snapshots.get("acordos_leniencia")
    if not isinstance(acordos, dict):
        result.append(unverified("acordo_leniencia_ativo", Escopo.CEDENTE, component="acordos_leniencia", path="acordos", fingerprint=fingerprint))
    else:
        active = bool(acordos.get("possui_acordo") or acordos.get("total_acordos"))
        result.append(finding("acordo_leniencia_ativo", Escopo.CEDENTE, active, component="acordos_leniencia", path="possui_acordo", fingerprint=fingerprint, state=Estado.CONFIRMADO if active else Estado.NEGATIVO_CONFIRMADO))
    for component, code in (("cnd_federal", "certidao_cnd_federal_pendente"), ("cndt_tst", "certidao_cndt_pendente"), ("fgts", "certidao_fgts_pendente")):
        cert = snapshots.get(component)
        if not isinstance(cert, dict):
            result.append(unverified(code, Escopo.CEDENTE, component=component, path="parsed_result", fingerprint=fingerprint))
            continue
        valid_until = cert.get("data_validade") or cert.get("validade")
        valid = cert.get("valida") is True
        pending = not valid
        result.append(finding(code, Escopo.CEDENTE, {"pendente": pending, "validade": valid_until}, component=component, path="valida", fingerprint=fingerprint, state=Estado.CONFIRMADO if pending else Estado.NEGATIVO_CONFIRMADO))
    document_types = _types(snapshots)
    has_balance = bool(document_types & BALANCO_DOCUMENT_TYPES)
    result.append(finding("balanco_ausente", Escopo.CEDENTE, not has_balance, component="catalogo_broadfactor", path="documentos_broadfactor", fingerprint=fingerprint, state=Estado.NEGATIVO_CONFIRMADO if has_balance else Estado.NAO_VERIFICADO, confidence=Confianca.ALTA if has_balance else Confianca.BAIXA))
    if has_balance:
        result.append(finding("balanco_catalogado_broadfactor", Escopo.CEDENTE, {"tipos": sorted(document_types & BALANCO_DOCUMENT_TYPES)}, component="catalogo_broadfactor", path="documentos_broadfactor", fingerprint=fingerprint))
    for code, field in (("capital_social_rs", "capital_social"), ("porte_cadastral", "porte")):
        value = brasil.get(field)
        if value is not None:
            result.append(finding(code, Escopo.CEDENTE, value, component="brasil_api", path=field, fingerprint=fingerprint))
    if brasil.get("data_abertura"):
        try:
            opened = datetime.fromisoformat(str(brasil["data_abertura"]).replace("Z", "+00:00")).date()
            age = round((datetime.now(timezone.utc).date() - opened).days / 365.25, 2)
            result.append(finding("idade_empresa_anos", Escopo.CEDENTE, age, component="brasil_api", path="data_abertura", fingerprint=fingerprint))
        except ValueError:
            pass
    if "qsa" in brasil:
        result.append(finding("qsa_estabilidade", Escopo.CEDENTE, {"qsa": brasil.get("qsa") or []}, component="brasil_api", path="qsa", fingerprint=fingerprint))
    return result
