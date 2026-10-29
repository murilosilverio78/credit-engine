"""Extrai e materializa certidoes capturadas da Broadfactor.

O fluxo e deliberadamente separado da captura de anexos: so documentos de
uma cotacao ainda ativa chegam aqui. CND estadual e municipal sao apenas
informativas; somente CND federal reutiliza o snapshot ja existente.
"""
from __future__ import annotations

import base64
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Callable

import structlog

from app.services.pdf_texto import Deadline, OCRUnavailableError, extract_pdf_pages, normalize_document, ocr_pdf


logger = structlog.get_logger()

CERTIFICATE_TYPES = frozenset({"CND_FEDERAL", "CND_ESTADUAL", "CND_MUNICIPAL"})
_RESULTS = frozenset({"negativa", "positiva_com_efeitos_negativa", "positiva"})
_CNPJ_PATTERN = re.compile(r"\b(\d{2}[.\s]?\d{3}[.\s]?\d{3}[/-]?\d{4}[-\s]?\d{2})\b")
_DATE_PATTERN = r"(\d{2}/\d{2}/\d{4})"


def _rows(result: Any) -> list[dict[str, Any]]:
    data = getattr(result, "data", result)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return [data] if isinstance(data, dict) else []


def _first(result: Any) -> dict[str, Any] | None:
    rows = _rows(result)
    return rows[0] if rows else None


def _digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _root(cnpj: str) -> str:
    return _digits(cnpj)[:8]


def _normalized(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn").upper()


def _date_value(value: object) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _first_match(patterns: tuple[str, ...], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return None


def _result(text: str) -> str | None:
    normalized = _normalized(text)
    if "POSITIVA COM EFEITOS DE NEGATIVA" in normalized or "POSITIVA C/ EFEITOS DE NEGATIVA" in normalized:
        return "positiva_com_efeitos_negativa"
    if "CERTIDAO POSITIVA" in normalized:
        return "positiva"
    if "CERTIDAO NEGATIVA" in normalized or "NEGATIVA DE DEBITOS" in normalized:
        return "negativa"
    return None


def _source_recognized(tipo: str, text: str) -> bool:
    normalized = _normalized(text)
    if "CERTIDAO" not in normalized:
        return False
    if tipo == "CND_FEDERAL":
        return any(marker in normalized for marker in (
            "TRIBUTOS FEDERAIS", "DIVIDA ATIVA DA UNIAO", "RECEITA FEDERAL",
            "PROCURADORIA-GERAL DA FAZENDA NACIONAL",
        ))
    if tipo == "CND_ESTADUAL":
        return any(marker in normalized for marker in (
            "SECRETARIA DA FAZENDA", "FAZENDA ESTADUAL", "GOVERNO DO ESTADO",
        ))
    return any(marker in normalized for marker in (
        "PREFEITURA", "MUNICIPAL", "MUNICIPIO",
    ))


def _issuer(tipo: str, text: str) -> str | None:
    normalized = _normalized(text)
    if tipo == "CND_FEDERAL":
        if "PROCURADORIA-GERAL DA FAZENDA NACIONAL" in normalized:
            return "Procuradoria-Geral da Fazenda Nacional"
        if "RECEITA FEDERAL" in normalized:
            return "Receita Federal do Brasil"
    if tipo == "CND_ESTADUAL":
        value = _first_match((r"(SECRETARIA\s+D[EA]\s+FAZENDA[^\n]{0,100})",), text)
        if value:
            return value
    if tipo == "CND_MUNICIPAL":
        value = _first_match((r"(PREFEITURA\s+MUNICIPAL\s+DE\s+[^\n]{1,100})",), text)
        if value:
            return value
    return None


def _extract_deterministic(tipo: str, text: str, expected_cnpj: str, *, today: date) -> dict[str, Any]:
    normalized_text = _normalized(text)
    cnpj_match = _CNPJ_PATTERN.search(text)
    cnpj = _digits(cnpj_match.group(1)) if cnpj_match else None
    emissao = _first_match((
        rf"(?:DATA\s+DE\s+EMISSAO|EMITID[AO]\s+EM|EMISSAO)\s*[:\-]?\s*{_DATE_PATTERN}",
    ), normalized_text)
    validade = _first_match((
        rf"(?:VALIDADE|VALIDA\s+ATE|VALIDO\s+ATE)\s*[:\-]?\s*{_DATE_PATTERN}",
    ), normalized_text)
    codigo = _first_match((
        r"(?:CODIGO)\s+(?:DE\s+)?(?:CONTROLE|AUTENTICIDADE)\s*[:\-]?\s*([A-Z0-9.\-/]{4,100})",
        r"(?:NUMERO)\s+(?:DA\s+)?CERTIDAO\s*[:\-]?\s*([A-Z0-9.\-/]{4,100})",
    ), normalized_text)
    uf = _first_match((r"\bUF\b\s*[:\-]?\s*([A-Z]{2})\b",), normalized_text)
    municipio = _first_match((
        r"(?:PREFEITURA\s+MUNICIPAL\s+DE|MUNICIPIO\s+DE|MUNICÍPIO\s+DE)\s+([A-Za-zÀ-ÿ '\-]{2,100})",
    ), text)
    expiration = _date_value(validade)
    source_ok = _source_recognized(tipo, text)
    cnpj_ok = bool(cnpj and _root(cnpj) == _root(expected_cnpj))
    current = bool(expiration and expiration >= today)
    result = _result(text)
    issuer = _issuer(tipo, text)
    return {
        "tipo": tipo,
        "resultado": result,
        "cnpj": cnpj,
        "data_emissao": _date_value(emissao),
        "data_validade": expiration,
        "codigo_controle": codigo,
        "orgao_emissor": issuer,
        "uf": uf,
        "municipio": municipio,
        # "valida" nunca significa resultado negativo: somente fonte,
        # titular e vigencia comprovados pelo documento.
        "valida": source_ok and cnpj_ok and current,
        "source_recognized": source_ok,
        "cnpj_compativel": cnpj_ok,
        "fonte_extracao": "deterministico",
        "confianca": "ALTA" if all((result, cnpj, expiration, issuer, source_ok)) else "MEDIA",
    }


def _needs_model_fallback(extraction: dict[str, Any]) -> bool:
    return not all((
        extraction.get("resultado"), extraction.get("cnpj"), extraction.get("data_validade"),
        extraction.get("orgao_emissor"), extraction.get("source_recognized"),
    ))


def _model_fallback(pdf: bytes, tipo: str) -> dict[str, Any]:
    """Uses a model only after deterministic text/OCR could not complete fields."""
    import anthropic
    from app.core.config import settings

    response = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY).messages.create(
        model=settings.CLAUDE_MODEL_EXTRACTION,
        max_tokens=500,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(pdf).decode("ascii"),
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "Extraia apenas JSON da certidao brasileira. Tipo esperado: " + tipo
                        + '. Campos: resultado (negativa|positiva_com_efeitos_negativa|positiva|null), '
                        'cnpj (somente digitos|null), data_emissao (DD/MM/AAAA|null), '
                        'data_validade (DD/MM/AAAA|null), codigo_controle (string|null), '
                        'orgao_emissor (string|null), uf (sigla|null), municipio (string|null).'
                    ),
                },
            ],
        }],
    )
    text = next((block.text for block in response.content if getattr(block, "type", None) == "text"), "")
    return json.loads(re.sub(r"^```json\s*|\s*```$", "", text.strip()))


def extract_certificate(
    content: bytes,
    tipo: str,
    expected_cnpj: str,
    *,
    today: date | None = None,
    model_fallback: Callable[[bytes, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extracts a certificate without persisting text, bytes or prompts."""
    if tipo not in CERTIFICATE_TYPES:
        raise ValueError("tipo_certidao_broadfactor_invalido")
    reference = today or date.today()
    pdf, _, _ = normalize_document(content)
    pages = extract_pdf_pages(pdf, Deadline.start(60))
    text = "\n".join(page for page in pages if page)
    if len(text) < 100:
        try:
            pages, _, _ = ocr_pdf(pdf, 4, Deadline.start(120))
            text = "\n".join(page for page in pages if page)
        except OCRUnavailableError:
            pass
    extracted = _extract_deterministic(tipo, text, expected_cnpj, today=reference)
    if not _needs_model_fallback(extracted):
        return extracted

    fallback = (model_fallback or _model_fallback)(pdf, tipo)
    if not isinstance(fallback, dict):
        raise ValueError("fallback_certidao_invalido")
    # Keep validation deterministic even when the model supplies missing fields.
    merged_text = text
    for key in ("resultado", "cnpj", "data_emissao", "data_validade", "codigo_controle", "orgao_emissor", "uf", "municipio"):
        if fallback.get(key) is not None:
            extracted[key] = fallback[key]
    extracted["cnpj"] = _digits(extracted.get("cnpj")) or None
    extracted["data_emissao"] = _date_value(extracted.get("data_emissao"))
    extracted["data_validade"] = _date_value(extracted.get("data_validade"))
    extracted["resultado"] = str(extracted.get("resultado") or "").lower() or None
    if extracted["resultado"] not in _RESULTS:
        extracted["resultado"] = _result(merged_text)
    extracted["source_recognized"] = bool(
        _source_recognized(tipo, merged_text) or extracted.get("orgao_emissor")
    )
    extracted["cnpj_compativel"] = bool(
        extracted.get("cnpj") and _root(str(extracted["cnpj"])) == _root(expected_cnpj)
    )
    extracted["valida"] = bool(
        extracted["source_recognized"]
        and extracted["cnpj_compativel"]
        and extracted.get("data_validade")
        and extracted["data_validade"] >= reference
    )
    extracted["fonte_extracao"] = "modelo"
    extracted["confianca"] = "MEDIA" if extracted["valida"] else "BAIXA"
    return extracted


def _certificate_row(extraction: dict[str, Any], *, cnpj: str, sha256: str) -> dict[str, Any]:
    return {
        "tipo": extraction["tipo"],
        "cnpj": cnpj,
        "sha256": sha256,
        "resultado": extraction.get("resultado"),
        "data_emissao": extraction.get("data_emissao").isoformat() if extraction.get("data_emissao") else None,
        "data_validade": extraction.get("data_validade").isoformat() if extraction.get("data_validade") else None,
        "codigo_controle": extraction.get("codigo_controle"),
        "orgao_emissor": extraction.get("orgao_emissor"),
        "uf": extraction.get("uf"),
        "municipio": extraction.get("municipio"),
        "valida": bool(extraction.get("valida")),
        "fonte_extracao": extraction.get("fonte_extracao", "indisponivel"),
        "confianca": extraction.get("confianca", "BAIXA"),
        "erro": None,
    }


def _materialize_federal_snapshot(
    database: Any,
    *,
    operation_id: str,
    document: dict[str, Any],
    extraction: dict[str, Any],
) -> None:
    parsed_result = {
        "status": "obtida",
        "fonte": "broadfactor",
        "storage_key": document.get("storage_key"),
        "filename": document.get("nome_arquivo"),
        "resultado": extraction.get("resultado"),
        "cnpj_certidao": extraction.get("cnpj"),
        "data_emissao": extraction.get("data_emissao").isoformat() if extraction.get("data_emissao") else None,
        "data_validade": extraction.get("data_validade").isoformat() if extraction.get("data_validade") else None,
        "valida": bool(extraction.get("valida")),
        "orgao_emissor": extraction.get("orgao_emissor"),
        "numero_certidao": extraction.get("codigo_controle"),
    }
    database.table("component_snapshots").upsert(
        {
            "operation_id": operation_id,
            "component": "cnd_federal",
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "parsed_result": parsed_result,
            "raw_result": {"fonte": "broadfactor", "sha256": document.get("sha256")},
            "error_message": None,
        },
        on_conflict="operation_id,component",
    ).execute()


def processar_certidoes_cotacao(
    cotacao_id: str,
    cnpj: str,
    operation_id: str | None = None,
    *,
    database: Any,
    storage: Any,
    today: date | None = None,
    model_fallback: Callable[[bytes, str], dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Processes only certificates already captured for one active quotation."""
    normalized_cnpj = _digits(cnpj)
    if len(normalized_cnpj) != 14:
        raise ValueError("cnpj_invalido")
    reference = today or date.today()
    quote = _first(
        database.table("cotacoes_broadfactor")
        .select("estagio")
        .eq("cotacao_id", cotacao_id)
        .maybe_single()
        .execute()
    )
    if not quote or str(quote.get("estagio") or "").upper() == "ENCERRADA":
        return {"processadas": 0, "reaproveitadas": 0, "falhas": 0, "informativas": 0}
    documents = _rows(
        database.table("documentos_cliente")
        .select("id,cotacao_id,cnpj,tipo,sha256,storage_key,nome_arquivo,capturado_em,status")
        .eq("cotacao_id", cotacao_id)
        .eq("cnpj", normalized_cnpj)
        .eq("status", "CAPTURADO")
        .execute()
    )
    summary = {"processadas": 0, "reaproveitadas": 0, "falhas": 0, "informativas": 0}
    federal_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for document in documents:
        tipo = str(document.get("tipo") or "").upper()
        sha256 = str(document.get("sha256") or "")
        storage_key = str(document.get("storage_key") or "")
        if tipo not in CERTIFICATE_TYPES or not re.fullmatch(r"[0-9a-f]{64}", sha256) or not storage_key:
            continue
        cached = _first(
            database.table("certidoes_broadfactor")
            .select("*")
            .eq("tipo", tipo).eq("cnpj", normalized_cnpj).eq("sha256", sha256)
            .maybe_single().execute()
        )
        try:
            if cached:
                extraction = {
                    **cached,
                    "tipo": tipo,
                    "cnpj": cached.get("cnpj"),
                    "data_emissao": _date_value(cached.get("data_emissao")),
                    "data_validade": _date_value(cached.get("data_validade")),
                }
                extraction["valida"] = bool(
                    cached.get("valida")
                    and extraction.get("data_validade")
                    and extraction["data_validade"] >= reference
                )
                summary["reaproveitadas"] += 1
            else:
                extraction = extract_certificate(
                    storage.download_document_at_key(storage_key), tipo, normalized_cnpj,
                    today=today, model_fallback=model_fallback,
                )
                database.table("certidoes_broadfactor").upsert(
                    _certificate_row(extraction, cnpj=normalized_cnpj, sha256=sha256),
                    on_conflict="tipo,cnpj,sha256",
                ).execute()
                summary["processadas"] += 1
            database.table("certidoes_broadfactor_origens").upsert(
                {
                    "tipo": tipo,
                    "cnpj": normalized_cnpj,
                    "sha256": sha256,
                    "documento_cliente_id": document["id"],
                    "cotacao_id": cotacao_id,
                    "capturado_em": document.get("capturado_em"),
                },
                on_conflict="tipo,cnpj,sha256,documento_cliente_id",
            ).execute()
            if tipo == "CND_FEDERAL":
                federal_candidates.append((document, extraction))
            else:
                summary["informativas"] += 1
        except Exception as exc:
            summary["falhas"] += 1
            logger.warning(
                "certidoes_broadfactor.extraction_failed",
                cotacao_id=cotacao_id,
                tipo=tipo,
                error_type=exc.__class__.__name__,
            )

    if operation_id and federal_candidates:
        # Prefer a valid/current certificate, then the one with the latest expiration.
        document, extraction = max(
            federal_candidates,
            key=lambda item: (
                bool(item[1].get("valida")),
                item[1].get("data_validade") or date.min,
                item[1].get("data_emissao") or date.min,
            ),
        )
        _materialize_federal_snapshot(
            database, operation_id=operation_id, document=document, extraction=extraction,
        )
    return summary
