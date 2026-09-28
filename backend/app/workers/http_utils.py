from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
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
    responses_429: int = 0
    responses_504: int = 0
    timeout_events: int = 0
    terminal_429: int = 0
    terminal_504: int = 0
    terminal_timeouts: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record_call(self) -> None:
        with self._lock:
            self.total_calls += 1

    def record_status(self, status: int) -> None:
        with self._lock:
            if status == 429:
                self.responses_429 += 1
            elif status == 504:
                self.responses_504 += 1

    def record_timeout(self) -> None:
        with self._lock:
            self.timeout_events += 1

    def record_terminal_429(self) -> None:
        with self._lock:
            self.terminal_429 += 1

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
                "respostas_429": self.responses_429,
                "respostas_504": self.responses_504,
                "eventos_timeout": self.timeout_events,
                "falhas_429_apos_retries": self.terminal_429,
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
        while True:
            with self._lock:
                now = time.monotonic()
                delay = max(self._next_allowed - now, 0.0)
                if not delay:
                    self._next_allowed = now + interval
                    return
            time.sleep(delay)

    def suspend(self, cooldown_seconds: float) -> None:
        cooldown = max(float(cooldown_seconds), 0.0)
        with self._lock:
            self._next_allowed = max(
                self._next_allowed,
                time.monotonic() + cooldown,
            )

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
    if status == 429:
        metrics.record_terminal_429()
    elif status == 504:
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


RETRY_BACKOFF_SECONDS = (2.2, 8.0, 20.0, 40.0)


def _jitter(value: float) -> float:
    return value * random.uniform(0.8, 1.2)


def _retry_delay(attempt: int) -> float:
    index = min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)
    return _jitter(RETRY_BACKOFF_SECONDS[index])


def _rate_limit_delay(response: httpx.Response) -> float:
    requested_delay: float | None = None
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            requested_delay = max(float(retry_after), 0.0)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                requested_delay = max(
                    (retry_at - datetime.now(timezone.utc)).total_seconds(),
                    0.0,
                )
            except (TypeError, ValueError, OverflowError):
                pass
    if requested_delay is None:
        requested_delay = _jitter(
            float(settings.PORTAL_429_DEFAULT_COOLDOWN_SECONDS)
        )
    return min(
        requested_delay,
        max(float(settings.PORTAL_429_MAX_COOLDOWN_SECONDS), 0.0),
    )


def fetch_json_with_retry(
    client: httpx.Client,
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    max_retries: int = 4,
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
                time.sleep(_retry_delay(attempt))
                continue
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status != 429 and status < 500:
                raise
            metrics = _PORTAL_CYCLE_METRICS.get()
            if metrics is not None:
                metrics.record_status(status)
            if status == 429:
                delay = _rate_limit_delay(exc.response)
                _PORTAL_RATE_LIMITER.suspend(delay)
                logger.warning(
                    "portal_transparencia.rate_limited",
                    url=url.split("?", 1)[0],
                    status=status,
                    tentativa=attempt + 1,
                    retry_after_header=exc.response.headers.get("retry-after"),
                    cooldown_s=delay,
                )
                if attempt >= max_retries:
                    _record_terminal_failure(status=status)
                    raise
                continue
            if attempt >= max_retries:
                _record_terminal_failure(status=status)
                raise
            delay = _retry_delay(attempt)
            logger.warning(
                "portal_transparencia.retry",
                url=url.split("?", 1)[0],
                status=status,
                tentativa=attempt + 1,
                delay_s=delay,
            )
            time.sleep(delay)
        except retry_exceptions as exc:
            metrics = _PORTAL_CYCLE_METRICS.get()
            if metrics is not None and isinstance(exc, httpx.TimeoutException):
                metrics.record_timeout()
            if attempt >= max_retries:
                _record_terminal_failure(exc)
                raise
            delay = _retry_delay(attempt)
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
