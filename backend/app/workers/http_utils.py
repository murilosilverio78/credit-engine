from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import Lock
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import structlog

from app.core.config import settings


logger = structlog.get_logger()


@dataclass
class PortalCycleMetrics:
    total_calls: int = 0
    terminal_504: int = 0
    terminal_timeouts: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record_call(self) -> None:
        with self._lock:
            self.total_calls += 1

    def record_terminal_504(self) -> None:
        with self._lock:
            self.terminal_504 += 1

    def record_terminal_timeout(self) -> None:
        with self._lock:
            self.terminal_timeouts += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "total_chamadas_portal": self.total_calls,
                "falhas_504_apos_retries": self.terminal_504,
                "timeouts_apos_retries": self.terminal_timeouts,
            }


class PortalRateLimiter:
    """Serialize Portal request starts across worker threads without bursts."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_allowed = 0.0

    def wait(self, min_interval_seconds: float) -> None:
        interval = max(float(min_interval_seconds), 0.0)
        with self._lock:
            now = time.monotonic()
            delay = max(self._next_allowed - now, 0.0)
            if delay:
                time.sleep(delay)
                now = time.monotonic()
            self._next_allowed = now + interval

    def reset(self) -> None:
        with self._lock:
            self._next_allowed = 0.0


_PORTAL_RATE_LIMITER = PortalRateLimiter()
_PORTAL_CYCLE_METRICS: ContextVar[PortalCycleMetrics | None] = ContextVar(
    "portal_cycle_metrics",
    default=None,
)


def set_portal_cycle_metrics(metrics: PortalCycleMetrics) -> Token:
    return _PORTAL_CYCLE_METRICS.set(metrics)


def reset_portal_cycle_metrics(token: Token) -> None:
    _PORTAL_CYCLE_METRICS.reset(token)


def _before_portal_call() -> None:
    _PORTAL_RATE_LIMITER.wait(settings.PORTAL_MIN_INTERVAL_SECONDS)
    metrics = _PORTAL_CYCLE_METRICS.get()
    if metrics is not None:
        metrics.record_call()


def _record_terminal_failure(exc: BaseException | None = None, *, status: int | None = None) -> None:
    metrics = _PORTAL_CYCLE_METRICS.get()
    if metrics is None:
        return
    if status == 504:
        metrics.record_terminal_504()
    elif isinstance(exc, httpx.TimeoutException):
        metrics.record_terminal_timeout()


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
        httpx.TimeoutException,
    )
    for attempt in range(max_retries + 1):
        response: httpx.Response | None = None
        try:
            _before_portal_call()
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
                _record_terminal_failure(status=status)
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
                _record_terminal_failure(exc)
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
