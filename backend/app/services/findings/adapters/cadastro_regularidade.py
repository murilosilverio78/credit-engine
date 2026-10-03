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


def _qsa_stability_indicators(qsa: Any) -> dict[str, Any]:
    socios = [socio for socio in qsa if isinstance(socio, dict)] if isinstance(qsa, list) else []
    dates = []
    for socio in socios:
        value = socio.get("data_entrada") or socio.get("data_inicio")
        try:
            dates.append(datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat())
        except (TypeError, ValueError):
            continue
    return {
        "socios_qtd": len(socios),
        "entrada_mais_recente": max(dates) if dates else None,
        "entrada_mais_antiga": min(dates) if dates else None,
        "tem_administrador": any("administr" in str(socio.get("qualificacao") or "").lower() for socio in socios),
    }


def emit_cadastro_regularidade(
    snapshots: dict[str, Any], *, fingerprint: str,
    operation: dict[str, Any] | None = None, database=None,
):
    result = []
    statuses = snapshots.get("__statuses__") or {}
    brasil = snapshots.get("brasil_api") or {}
    pessoa = snapshots.get("pessoa_juridica") or {}
    situacao = brasil.get("situacao_cadastral") or pessoa.get("situacao_cadastral") or pessoa.get("situacao")
    if situacao is None:
        result.append(unverified("cadastro_inativo", Escopo.CEDENTE, component="brasil_api", path="situacao_cadastral", fingerprint=fingerprint))
    else:
        active = str(situacao).strip().upper() == "ATIVA"
        result.append(finding("cadastro_inativo", Escopo.CEDENTE, not active, component="brasil_api" if brasil else "pessoa_juridica", path="situacao_cadastral", fingerprint=fingerprint, state=Estado.NEGATIVO_CONFIRMADO if active else Estado.CONFIRMADO))
    sanction_components = ("ceis", "cnep", "cepim", "ceaf")
    sanction_sources = ("pessoa_juridica", "ceis", "cnep", "cepim", "acordos_leniencia")
    from app.workers.tasks.score_engine import _component_list, _has_records, _is_active_record
    sanction_details = []
    for component in sanction_components:
        item = snapshots.get(component)
        if isinstance(item, dict) and _has_records(item, "possui_sancao", "total_registros"):
            records = _component_list(item)
            if records and not any(isinstance(record, dict) and _is_active_record(record) for record in records):
                continue
            sanction_details.append({"componente": component, "registros": item.get("registros") or []})
    if pessoa.get("possui_sancao") or any(pessoa.get(f"sancionado_{name}") for name in ("ceis", "cnep", "cepim", "ceaf")):
        sanction_details.append({"componente": "pessoa_juridica", "flags": True})
    # A confirmed sanction is decisive even if another source failed.  Only a
    # negative conclusion requires every available source to have completed.
    sources_ready = all(statuses.get(source, "missing") == "completed" for source in sanction_sources) and not pessoa.get("erro")
    result.append(
        finding("sancao_ativa", Escopo.CEDENTE, sanction_details, component="pessoa_juridica", path="flags_sancao", fingerprint=fingerprint, state=Estado.CONFIRMADO)
        if sanction_details else (
            finding("sancao_ativa", Escopo.CEDENTE, [], component="pessoa_juridica", path="flags_sancao", fingerprint=fingerprint, state=Estado.NEGATIVO_CONFIRMADO)
            if sources_ready else unverified("sancao_ativa", Escopo.CEDENTE, component="pessoa_juridica", path="flags_sancao", fingerprint=fingerprint)
        )
    )
    acordos = snapshots.get("acordos_leniencia")
    if isinstance(acordos, dict):
        records = _component_list(acordos)
        active = _has_records(acordos, "possui_acordo", "total_registros", "total_acordos") and (not records or any(isinstance(record, dict) and _is_active_record(record) for record in records))
        if active:
            result.append(finding("acordo_leniencia_ativo", Escopo.CEDENTE, True, component="acordos_leniencia", path="possui_acordo", fingerprint=fingerprint, state=Estado.CONFIRMADO))
        elif statuses.get("acordos_leniencia", "missing") == "completed":
            result.append(finding("acordo_leniencia_ativo", Escopo.CEDENTE, False, component="acordos_leniencia", path="possui_acordo", fingerprint=fingerprint, state=Estado.NEGATIVO_CONFIRMADO))
        else:
            result.append(unverified("acordo_leniencia_ativo", Escopo.CEDENTE, component="acordos_leniencia", path="acordos", fingerprint=fingerprint))
    else:
        result.append(unverified("acordo_leniencia_ativo", Escopo.CEDENTE, component="acordos_leniencia", path="acordos", fingerprint=fingerprint))
    for component, code in (("cnd_federal", "certidao_cnd_federal_pendente"), ("cndt_tst", "certidao_cndt_pendente"), ("fgts", "certidao_fgts_pendente")):
        cert = snapshots.get(component)
        status = statuses.get(component, "completed" if isinstance(cert, dict) else "inexistente")
        if not isinstance(cert, dict) or not cert:
            value = {"estado": "ausente", "status_componente": status, "validade": None, "fator": 0.0}
            item = finding(code, Escopo.CEDENTE, value, component=component, path="parsed_result", fingerprint=fingerprint, state=Estado.CONFIRMADO, confidence=Confianca.ALTA)
        else:
            from app.workers.tasks.score_engine import _certidao_estado
            estado, fator, _flags = _certidao_estado(cert)
            item = finding(code, Escopo.CEDENTE, {"estado": estado, "status_componente": status, "validade": cert.get("data_validade") or cert.get("validade"), "fator": fator}, component=component, path="valida", fingerprint=fingerprint, state=Estado.NEGATIVO_CONFIRMADO if estado == "negativa" else Estado.CONFIRMADO, confidence=Confianca.ALTA)
        item.evidencia[0]["status_componente"] = status
        result.append(item)
    # Exact score semantics: extraction, operation documents and quote catalog
    # are recursively scanned by the same helper used by score_engine.
    from app.workers.tasks.score_engine import _document_types
    extraction_types = _document_types(snapshots.get("contrato_extracao"))
    operation_types = _document_types(snapshots.get("documentos_operacao"))
    quote_types = _document_types(snapshots.get("catalogo_broadfactor"))
    document_types = extraction_types | operation_types | quote_types
    has_balance = bool(document_types & BALANCO_DOCUMENT_TYPES)
    # The score only scans document sources; sanction availability cannot make
    # this presence fact unknown.
    result.append(finding("balanco_ausente", Escopo.CEDENTE, not has_balance, component="catalogo_broadfactor", path="documentos_broadfactor", fingerprint=fingerprint, state=Estado.NEGATIVO_CONFIRMADO if has_balance else Estado.CONFIRMADO, confidence=Confianca.ALTA))
    if has_balance:
        origins = {}
        for document_type in sorted(document_types & BALANCO_DOCUMENT_TYPES):
            origins[document_type] = [
                source for source, values in (("cotacao", quote_types), ("extracao", extraction_types), ("documentos_operacao", operation_types))
                if document_type in values
            ]
        result.append(finding("balanco_catalogado_broadfactor", Escopo.CEDENTE, {"tipos": sorted(origins), "fontes": origins}, component="catalogo_broadfactor", path="documentos_broadfactor,contrato_extracao,documentos_operacao", fingerprint=fingerprint))
    for code, field in (("capital_social_rs", "capital_social"), ("porte_cadastral", "porte")):
        value = brasil.get(field)
        if value is not None:
            result.append(finding(code, Escopo.CEDENTE, value, component="brasil_api", path=field, fingerprint=fingerprint))
    if brasil.get("data_abertura"):
        try:
            opened = datetime.fromisoformat(str(brasil["data_abertura"]).replace("Z", "+00:00")).date()
            reference = datetime.now(timezone.utc).date()
            age = (reference - opened).days / 365.25
            item = finding("idade_empresa_anos", Escopo.CEDENTE, age, component="brasil_api", path="data_abertura", fingerprint=fingerprint)
            item.evidencia[0]["data_referencia"] = reference.isoformat()
            result.append(item)
        except ValueError:
            pass
    if "qsa" in brasil:
        result.append(finding("qsa_estabilidade", Escopo.CEDENTE, _qsa_stability_indicators(brasil.get("qsa")), component="brasil_api", path="qsa", fingerprint=fingerprint))
    # Keep these facts exactly aligned with the deterministic score helpers.
    from app.workers.tasks.score_engine import _atividade_restrita, _is_empresarial
    cadastro = brasil or pessoa
    natureza = cadastro.get("natureza_juridica") if isinstance(cadastro, dict) else None
    result.append(finding(
        "natureza_juridica_empresarial", Escopo.CEDENTE,
        _is_empresarial(natureza), component="brasil_api" if brasil else "pessoa_juridica",
        path="natureza_juridica", fingerprint=fingerprint,
    ))
    result.append(finding(
        "atividade_restrita", Escopo.CEDENTE,
        _atividade_restrita(cadastro if isinstance(cadastro, dict) else {}),
        component="brasil_api" if brasil else "pessoa_juridica",
        path="atividade_principal", fingerprint=fingerprint,
    ))
    return result
