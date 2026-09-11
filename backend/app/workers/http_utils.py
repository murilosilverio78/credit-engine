import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import structlog

from app.core.config import settings


logger = structlog.get_logger()


class PortalRespostaVaziaError(RuntimeError):
    """Portal returned HTTP 200 without a usable response body."""


def _pagination_params(url: str, params: dict[str, Any] | None) -> dict[str, Any]:
    values = dict(parse_qsl(urlsplit(url).query))
    values.update(params or {})
    return {
        key: value
        for key, value in values.items()
        if key.lower() in {"pagina", "page", "size", "tamanho"}
    }


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 15.0)
    return min(float(2 ** attempt), 15.0)


def fetch_json_with_retry(
    client: httpx.Client,
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> list | dict:
    """Fetch JSON with retry/backoff for transient Portal da Transparencia errors."""
    retry_exceptions = (
        httpx.ConnectError,
        httpx.RemoteProtocolError,
        httpx.ReadTimeout,
    )
    for attempt in range(max_retries + 1):
        response: httpx.Response | None = None
        try:
            response = client.get(url, headers=headers, params=params)
            if response.status_code in {429} or response.status_code >= 500:
                response.raise_for_status()
            response.raise_for_status()
            if not response.content or not response.text.strip():
                logger.warning(
                    "portal.empty_body",
                    endpoint=url.split("?", 1)[0],
                    params_pagina=_pagination_params(url, params),
                    tentativa=attempt + 1,
                )
                if settings.PORTAL_EMPTY_BODY_POLICY == "empty":
                    return []
                if attempt >= max_retries:
                    raise PortalRespostaVaziaError(
                        f"Portal retornou corpo vazio em {url.split('?', 1)[0]} "
                        f"apos {max_retries + 1} tentativas"
                    )
                time.sleep(_retry_delay(response, attempt))
                continue
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status != 429 and status < 500:
                raise
            if attempt >= max_retries:
                raise
            delay = _retry_delay(exc.response, attempt)
            logger.warning(
                "portal_transparencia.retry",
                url=url.split("?", 1)[0],
                status=status,
                tentativa=attempt + 1,
                delay_s=delay,
            )
            time.sleep(delay)
        except retry_exceptions as exc:
            if attempt >= max_retries:
                raise
            delay = _retry_delay(response, attempt)
            logger.warning(
                "portal_transparencia.retry",
                url=url.split("?", 1)[0],
                status=None,
                tentativa=attempt + 1,
                delay_s=delay,
                error=type(exc).__name__,
            )
            time.sleep(delay)

    raise RuntimeError("retry loop exited unexpectedly")
