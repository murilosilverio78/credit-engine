"""Narrow VPS-only HTTP service for the TST browser collector.

This module must run only in Brazil.  It never receives R2 credentials and
does not persist application data; the Railway caller owns R2 and lineage.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .client import CndtClient, CndtSettings


logger = logging.getLogger(__name__)
app = FastAPI(title="TST CNDT VPS", docs_url=None, redoc_url=None, openapi_url=None)
_ISSUANCE_LOCK = asyncio.Lock()


class IssueRequest(BaseModel):
    cnpj: str


def _configured_token() -> str:
    return os.environ.get("CNDT_PROXY_TOKEN", "")


def _authorized(received: str | None) -> bool:
    expected = _configured_token()
    return bool(expected and received and hmac.compare_digest(received, expected))


def _collector() -> CndtClient:
    return CndtClient(CndtSettings(
        output_dir=Path(os.environ.get("CNDT_STATE_DIR", "/var/lib/tst-cndt")),
        captcha_timeout_seconds=int(os.environ.get("CNDT_CAPTCHA_TIMEOUT_SECONDS", "120")),
        portal_timeout_ms=int(os.environ.get("CNDT_PORTAL_TIMEOUT_MS", "30000")),
        max_captcha_attempts=int(os.environ.get("CNDT_MAX_CAPTCHA_ATTEMPTS", "3")),
        cache_safety_days=int(os.environ.get("CNDT_CACHE_SAFETY_DAYS", "7")),
    ))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/cndt/emitir")
async def emitir_cndt(
    request: IssueRequest,
    x_proxy_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Issue/reuse one CNDT, serialising browser work globally on this VPS."""
    if not _authorized(x_proxy_token):
        raise HTTPException(status_code=401, detail="unauthorized")
    async with _ISSUANCE_LOCK:
        result = await asyncio.to_thread(_collector().issue, request.cnpj)
    if result.get("status") != "obtida":
        return {"result": result}
    try:
        pdf = Path(str(result["storage_key"])).read_bytes()
    except (OSError, KeyError, TypeError):
        return {"result": {"status": "falha", "erro": "PDF_NAO_GERADO", "fonte": "tst"}}
    # The PDF is returned only over the authenticated TLS channel. Never log it.
    return {"result": result, "pdf_base64": base64.b64encode(pdf).decode("ascii")}
