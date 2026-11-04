"""Railway-side CNDT collection through the authenticated Brazilian VPS.

The service intentionally owns R2, snapshot and database lineage while the
VPS owns only Playwright/2captcha.  Every public function converts operational
errors to an unsuccessful result so the existing manual-upload path remains
available.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.integrations.tst_cndt.parser import digits

logger = logging.getLogger(__name__)
_MAX_PDF_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class CndtCollectionResult:
    obtained: bool
    source: str
    error: str | None = None


def _rows(result: Any) -> list[dict[str, Any]]:
    data = getattr(result, "data", result)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return [data] if isinstance(data, dict) else []


def _first(result: Any) -> dict[str, Any] | None:
    rows = _rows(result)
    return rows[0] if rows else None


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _iso(value: Any) -> str | None:
    parsed = _date(value)
    return parsed.isoformat() if parsed else None


class TstCndtProxyClient:
    """Small client with a bounded request and no sensitive logging."""

    def __init__(self, *, timeout_seconds: float | None = None):
        self.timeout_seconds = timeout_seconds

    def issue(self, cnpj: str) -> tuple[dict[str, Any], bytes | None]:
        if not settings.CNDT_PROXY_URL or not settings.CNDT_PROXY_TOKEN:
            return {"status": "falha", "erro": "PORTAL_INDISPONIVEL", "fonte": "tst"}, None
        try:
            response = httpx.post(
                settings.CNDT_PROXY_URL.rstrip("/") + "/v1/cndt/emitir",
                headers={"X-Proxy-Token": settings.CNDT_PROXY_TOKEN},
                json={"cnpj": cnpj},
                timeout=max(
                    float(
                        self.timeout_seconds
                        if self.timeout_seconds is not None
                        else settings.CNDT_PROXY_TIMEOUT_SECONDS
                    ),
                    1.0,
                ),
                follow_redirects=False,
            )
            if response.status_code != 200:
                return {"status": "falha", "erro": "PORTAL_INDISPONIVEL", "fonte": "tst"}, None
            envelope = response.json()
            result = envelope.get("result") if isinstance(envelope, dict) else None
            if not isinstance(result, dict):
                return {"status": "falha", "erro": "PDF_NAO_GERADO", "fonte": "tst"}, None
            encoded = envelope.get("pdf_base64")
            if result.get("status") != "obtida":
                return result, None
            if not isinstance(encoded, str):
                return {"status": "falha", "erro": "PDF_NAO_GERADO", "fonte": "tst"}, None
            pdf = base64.b64decode(encoded, validate=True)
            if not pdf.startswith(b"%PDF") or len(pdf) > _MAX_PDF_BYTES:
                return {"status": "falha", "erro": "PDF_NAO_GERADO", "fonte": "tst"}, None
            return result, pdf
        except (ValueError, httpx.HTTPError):
            return {"status": "falha", "erro": "PORTAL_INDISPONIVEL", "fonte": "tst"}, None


def _valid_result(result: dict[str, Any], cnpj: str, today: date) -> bool:
    return bool(
        result.get("status") == "obtida"
        and result.get("fonte") == "tst"
        and result.get("valida") is True
        and digits(result.get("cnpj_certidao")) == cnpj
        and _date(result.get("data_validade"))
        and _date(result.get("data_validade")) >= today
        and result.get("resultado") in {"negativa", "positiva", "positiva_com_efeitos_negativa"}
    )


def _snapshot(result: dict[str, Any], storage_key: str) -> dict[str, Any]:
    return {
        "status": "obtida",
        "fonte": "tst",
        "storage_key": storage_key,
        "filename": result.get("filename"),
        "resultado": result.get("resultado"),
        "cnpj_certidao": digits(result.get("cnpj_certidao")),
        "data_emissao": result.get("data_emissao"),
        "data_validade": result.get("data_validade"),
        "valida": True,
        "orgao_emissor": "Tribunal Superior do Trabalho",
        "numero_certidao": result.get("numero_certidao"),
    }


def _materialize(database: Any, operation_id: str, parsed: dict[str, Any], *, cache_hit: bool) -> None:
    database.table("component_snapshots").upsert(
        {
            "operation_id": operation_id,
            "component": "cndt_tst",
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "parsed_result": parsed,
            "raw_result": {"fonte": "tst", "cache_hit": cache_hit},
            "error_message": None,
        },
        on_conflict="operation_id,component",
    ).execute()


def _record_lineage(database: Any, operation_id: str, cnpj: str, *, cache_hit: bool, sha256: str) -> None:
    database.table("certidoes_cndt_tst_lineage").insert({
        "operation_id": operation_id,
        "cnpj": cnpj,
        "sha256": sha256,
        "cache_hit": cache_hit,
        "fonte": "tst",
    }).execute()


def _cached(database: Any, cnpj: str, today: date) -> dict[str, Any] | None:
    row = _first(
        database.table("certidoes_cndt_tst_cache")
        .select("*").eq("cnpj", cnpj).maybe_single().execute()
    )
    safety = today + timedelta(days=max(int(settings.CNDT_CACHE_SAFETY_DAYS), 0))
    if not row or row.get("valida") is not True or (_date(row.get("data_validade")) or date.min) < safety:
        return None
    return row


def _cache_result(result: dict[str, Any], storage_key: str, sha256: str) -> dict[str, Any]:
    return {
        "cnpj": digits(result.get("cnpj_certidao")),
        "sha256": sha256,
        "storage_key": storage_key,
        "filename": result.get("filename"),
        "resultado": result.get("resultado"),
        "data_emissao": _iso(result.get("data_emissao")),
        "data_validade": _iso(result.get("data_validade")),
        "numero_certidao": result.get("numero_certidao"),
        "orgao_emissor": "Tribunal Superior do Trabalho",
        "valida": True,
        "emitida_em": datetime.now(timezone.utc).isoformat(),
    }


def collect_cndt_for_operation(
    operation_id: str,
    *,
    database: Any,
    storage: Any | None = None,
    proxy: TstCndtProxyClient | Any | None = None,
    today: date | None = None,
) -> CndtCollectionResult:
    """Materialize CNDT or return failure so the caller can keep manual upload."""
    reference = today or date.today()
    try:
        operation = _first(
            database.table("operations").select("cnpj").eq("id", operation_id).maybe_single().execute()
        ) or {}
        cnpj = digits(operation.get("cnpj"))
        if len(cnpj) != 14:
            return CndtCollectionResult(False, "tst", "CNPJ_INVALIDO")
        cached = _cached(database, cnpj, reference)
        if cached:
            parsed = _snapshot({
                **cached, "status": "obtida", "fonte": "tst", "cnpj_certidao": cnpj,
            }, str(cached["storage_key"]))
            _materialize(database, operation_id, parsed, cache_hit=True)
            _record_lineage(database, operation_id, cnpj, cache_hit=True, sha256=str(cached["sha256"]))
            return CndtCollectionResult(True, "cache")
        result, pdf = (proxy or TstCndtProxyClient()).issue(cnpj)
        if not _valid_result(result, cnpj, reference) or not pdf:
            return CndtCollectionResult(False, "tst", str(result.get("erro") or "PDF_NAO_GERADO"))
        digest = hashlib.sha256(pdf).hexdigest()
        storage_key = f"clientes/{cnpj}/documentos/{digest}.pdf"
        if storage is None:
            from app.services.storage_service import StorageService
            storage = StorageService()
        storage.upload_document_at_key(storage_key, pdf, "application/pdf")
        cache = _cache_result(result, storage_key, digest)
        database.table("certidoes_cndt_tst_cache").upsert(cache, on_conflict="cnpj").execute()
        parsed = _snapshot(result, storage_key)
        _materialize(database, operation_id, parsed, cache_hit=False)
        _record_lineage(database, operation_id, cnpj, cache_hit=False, sha256=digest)
        return CndtCollectionResult(True, "tst")
    except Exception as exc:
        logger.warning("cndt.collection_failed", extra={"operation_id": operation_id, "error_type": exc.__class__.__name__})
        return CndtCollectionResult(False, "tst", "PORTAL_INDISPONIVEL")
