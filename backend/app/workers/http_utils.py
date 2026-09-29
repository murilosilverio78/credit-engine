from collections import deque
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
from threading import Lock
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import structlog

from app.core.config import settings


logger = structlog.get_logger()


def portal_api_url(path: str) -> str:
    base_url = settings.PORTAL_BASE_URL.rstrip("/")
    return f"{base_url}/api-de-dados/{path.lstrip('/')}"


def portal_headers(api_token: str | None = None) -> dict[str, str]:
    headers = {
        "chave-api-dados": api_token or settings.PORTAL_TRANSPARENCIA_TOKEN,
    }
    if settings.PORTAL_PROXY_TOKEN:
        headers["X-Proxy-Token"] = settings.PORTAL_PROXY_TOKEN
    return headers


class PortalGuardrailUnavailable(RuntimeError):
    """A local consumption guardrail prevented a Portal request."""


class PortalMinuteWaitExceeded(PortalGuardrailUnavailable):
    pass


class PortalCycleLimitExceeded(PortalGuardrailUnavailable):
    pass


class PortalDailyLimitExceeded(PortalGuardrailUnavailable):
    pass


class PortalDailyCounterUnavailable(PortalGuardrailUnavailable):
    pass


@dataclass(frozen=True)
class DailyClaim:
    allowed: bool
    consumed: int
    alert_now: bool = False
    usage_date: str | None = None


class PortalDailyUsageStore:
    """Persistent daily counter backed by atomic Supabase RPCs."""

    def __init__(self) -> None:
        self._lock = Lock()

    @staticmethod
    def _row(data: Any) -> dict[str, Any]:
        if isinstance(data, list):
            return data[0] if data else {}
        return data if isinstance(data, dict) else {}

    def claim(self, limit: int) -> DailyClaim:
        try:
            from app.core.database import supabase

            with self._lock:
                result = supabase.rpc(
                    "claim_portal_daily_request",
                    {"p_limit": max(int(limit), 0)},
                ).execute()
            row = self._row(result.data)
            return DailyClaim(
                allowed=bool(row.get("allowed")),
                consumed=int(row.get("consumed") or 0),
                alert_now=bool(row.get("alert_now")),
                usage_date=str(row.get("usage_date") or "") or None,
            )
        except PortalGuardrailUnavailable:
            raise
        except Exception as exc:
            raise PortalDailyCounterUnavailable(
                f"portal_guardrail:contador_diario_indisponivel:{exc}"
            ) from exc

    def current(self) -> DailyClaim:
        try:
            from app.core.database import supabase

            with self._lock:
                result = supabase.rpc("get_portal_daily_usage").execute()
            row = self._row(result.data)
            return DailyClaim(
                allowed=True,
                consumed=int(row.get("consumed") or 0),
                alert_now=False,
                usage_date=str(row.get("usage_date") or "") or None,
            )
        except Exception as exc:
            raise PortalDailyCounterUnavailable(
                f"portal_guardrail:contador_diario_indisponivel:{exc}"
            ) from exc


@dataclass
class PortalCycleMetrics:
    total_calls: int = 0
    responses_429: int = 0
    responses_504: int = 0
    timeout_events: int = 0
    terminal_429: int = 0
    terminal_504: int = 0
    terminal_timeouts: int = 0
    daily_consumed: int = 0
    peak_per_minute: int = 0
    minute_guardrail_hits: int = 0
    cycle_guardrail_hits: int = 0
    daily_guardrail_hits: int = 0
    cycle_exceeded: bool = False
    _cycle_alerted: bool = False
    _lock: Lock = field(default_factory=Lock, repr=False)

    def can_call(self, limit: int) -> bool:
        with self._lock:
            return self.total_calls < max(int(limit), 0)

    def record_call(self, *, daily_consumed: int, minute_consumed: int) -> bool:
        with self._lock:
            self.total_calls += 1
            self.daily_consumed = daily_consumed
            self.peak_per_minute = max(self.peak_per_minute, minute_consumed)
            cycle_limit = max(int(settings.PORTAL_MAX_POR_CICLO), 0)
            threshold = math.ceil(cycle_limit * 0.7)
            should_alert = (
                not self._cycle_alerted
                and threshold > 0
                and self.total_calls >= threshold
            )
            if should_alert:
                self._cycle_alerted = True
            if self.total_calls >= cycle_limit and not self.cycle_exceeded:
                self.cycle_exceeded = True
                self.cycle_guardrail_hits += 1
            return should_alert

    def update_usage(self, *, daily_consumed: int, minute_consumed: int) -> None:
        with self._lock:
            self.daily_consumed = daily_consumed
            self.peak_per_minute = max(self.peak_per_minute, minute_consumed)

    def record_guardrail(self, guardrail: str) -> None:
        with self._lock:
            if guardrail == "minuto":
                self.minute_guardrail_hits += 1
            elif guardrail == "ciclo":
                if not self.cycle_exceeded:
                    self.cycle_guardrail_hits += 1
                self.cycle_exceeded = True
            elif guardrail == "dia":
                self.daily_guardrail_hits += 1

    def is_cycle_exceeded(self) -> bool:
        with self._lock:
            return self.cycle_exceeded

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
                "consumo_ciclo": self.total_calls,
                "consumo_dia": self.daily_consumed,
                "pico_por_minuto": self.peak_per_minute,
                "guardrail_minuto_acionado": self.minute_guardrail_hits,
                "guardrail_ciclo_acionado": self.cycle_guardrail_hits,
                "guardrail_dia_acionado": self.daily_guardrail_hits,
            }


class PortalRateLimiter:
    """Serialize Portal request starts across worker threads without bursts."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_allowed = 0.0
        self._minute_calls: deque[float] = deque()
        self._minute_alerted = False

    def wait(
        self,
        min_interval_seconds: float,
        *,
        max_per_minute: int | None = None,
        max_wait_seconds: float | None = None,
        cycle_metrics: PortalCycleMetrics | None = None,
        daily_store: PortalDailyUsageStore | None = None,
    ) -> None:
        interval = max(float(min_interval_seconds), 0.0)
        minute_limit = None if max_per_minute is None else max(int(max_per_minute), 0)
        wait_limit = (
            float("inf")
            if max_wait_seconds is None
            else max(float(max_wait_seconds), 0.0)
        )
        wait_started = time.monotonic()
        minute_guardrail_recorded = False
        while True:
            with self._lock:
                now = time.monotonic()
                while self._minute_calls and self._minute_calls[0] <= now - 60.0:
                    self._minute_calls.popleft()

                threshold = (
                    math.ceil(minute_limit * 0.7)
                    if minute_limit is not None
                    else 0
                )
                if threshold and len(self._minute_calls) < threshold:
                    self._minute_alerted = False

                minute_delay = 0.0
                if minute_limit is not None and len(self._minute_calls) >= minute_limit:
                    minute_delay = (
                        60.0
                        if not self._minute_calls
                        else max(self._minute_calls[0] + 60.0 - now, 0.0)
                    )
                    if not minute_guardrail_recorded:
                        minute_guardrail_recorded = True
                        if cycle_metrics is not None:
                            cycle_metrics.record_guardrail("minuto")

                elapsed = now - wait_started
                if minute_delay > max(wait_limit - elapsed, 0.0):
                    raise PortalMinuteWaitExceeded(
                        "portal_guardrail:minuto_espera_excedida"
                    )

                spacing_delay = max(self._next_allowed - now, 0.0)
                delay = max(spacing_delay, minute_delay)
                if not delay:
                    if cycle_metrics is not None and not cycle_metrics.can_call(
                        settings.PORTAL_MAX_POR_CICLO
                    ):
                        cycle_metrics.record_guardrail("ciclo")
                        raise PortalCycleLimitExceeded(
                            "portal_guardrail:ciclo_excedido"
                        )

                    daily_claim = (
                        daily_store.claim(settings.PORTAL_MAX_POR_DIA)
                        if daily_store is not None
                        else DailyClaim(True, 0)
                    )
                    if not daily_claim.allowed:
                        if cycle_metrics is not None:
                            cycle_metrics.update_usage(
                                daily_consumed=daily_claim.consumed,
                                minute_consumed=len(self._minute_calls),
                            )
                            cycle_metrics.record_guardrail("dia")
                        logger.error(
                            "portal_guardrail.dia_excedido",
                            consumo=daily_claim.consumed,
                            limite=settings.PORTAL_MAX_POR_DIA,
                            data=daily_claim.usage_date,
                        )
                        raise PortalDailyLimitExceeded(
                            "portal_guardrail:dia_excedido"
                        )

                    self._minute_calls.append(now)
                    minute_consumed = len(self._minute_calls)
                    self._next_allowed = now + interval

                    if (
                        threshold
                        and minute_consumed >= threshold
                        and not self._minute_alerted
                    ):
                        self._minute_alerted = True
                        logger.warning(
                            "portal_guardrail.alerta",
                            guardrail="minuto",
                            consumo=minute_consumed,
                            limite=minute_limit,
                        )
                    if daily_claim.alert_now:
                        logger.warning(
                            "portal_guardrail.alerta",
                            guardrail="dia",
                            consumo=daily_claim.consumed,
                            limite=settings.PORTAL_MAX_POR_DIA,
                        )
                    if cycle_metrics is not None:
                        cycle_alert = cycle_metrics.record_call(
                            daily_consumed=daily_claim.consumed,
                            minute_consumed=minute_consumed,
                        )
                        if cycle_alert:
                            logger.warning(
                                "portal_guardrail.alerta",
                                guardrail="ciclo",
                                consumo=cycle_metrics.snapshot()["consumo_ciclo"],
                                limite=settings.PORTAL_MAX_POR_CICLO,
                            )
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
            self._minute_calls.clear()
            self._minute_alerted = False

    def current_minute_usage(self) -> int:
        with self._lock:
            now = time.monotonic()
            while self._minute_calls and self._minute_calls[0] <= now - 60.0:
                self._minute_calls.popleft()
            return len(self._minute_calls)


_PORTAL_RATE_LIMITER = PortalRateLimiter()
_PORTAL_DAILY_USAGE = PortalDailyUsageStore()
_PORTAL_CYCLE_METRICS: ContextVar[PortalCycleMetrics | None] = ContextVar(
    "portal_cycle_metrics",
    default=None,
)
_ACTIVE_CYCLE_LOCK = Lock()
_ACTIVE_CYCLE_METRICS: PortalCycleMetrics | None = None
_LAST_CYCLE_METRICS: PortalCycleMetrics | None = None


def set_portal_cycle_metrics(metrics: PortalCycleMetrics) -> Token:
    global _ACTIVE_CYCLE_METRICS
    with _ACTIVE_CYCLE_LOCK:
        _ACTIVE_CYCLE_METRICS = metrics
    return _PORTAL_CYCLE_METRICS.set(metrics)


def reset_portal_cycle_metrics(token: Token) -> None:
    global _ACTIVE_CYCLE_METRICS, _LAST_CYCLE_METRICS
    metrics = _PORTAL_CYCLE_METRICS.get()
    _PORTAL_CYCLE_METRICS.reset(token)
    with _ACTIVE_CYCLE_LOCK:
        _LAST_CYCLE_METRICS = metrics
        if _ACTIVE_CYCLE_METRICS is metrics:
            _ACTIVE_CYCLE_METRICS = None


def current_portal_cycle_metrics() -> PortalCycleMetrics | None:
    return _PORTAL_CYCLE_METRICS.get()


def portal_guardrail_snapshot() -> dict[str, Any]:
    with _ACTIVE_CYCLE_LOCK:
        metrics = _ACTIVE_CYCLE_METRICS or _LAST_CYCLE_METRICS
        cycle_active = _ACTIVE_CYCLE_METRICS is not None
    try:
        daily = _PORTAL_DAILY_USAGE.current()
        daily_data = {
            "consumo": daily.consumed,
            "limite": settings.PORTAL_MAX_POR_DIA,
            "data": daily.usage_date,
            "erro": None,
        }
    except PortalDailyCounterUnavailable as exc:
        daily_data = {
            "consumo": None,
            "limite": settings.PORTAL_MAX_POR_DIA,
            "data": None,
            "erro": str(exc),
        }
    cycle_snapshot = metrics.snapshot() if metrics is not None else {}
    return {
        "minuto": {
            "consumo": _PORTAL_RATE_LIMITER.current_minute_usage(),
            "limite": settings.PORTAL_MAX_POR_MINUTO,
        },
        "ciclo": {
            "consumo": cycle_snapshot.get("consumo_ciclo", 0),
            "limite": settings.PORTAL_MAX_POR_CICLO,
            "ativo": cycle_active,
        },
        "dia": daily_data,
    }


def acquire_portal_call() -> None:
    metrics = _PORTAL_CYCLE_METRICS.get()
    _PORTAL_RATE_LIMITER.wait(
        settings.PORTAL_MIN_INTERVAL_SECONDS,
        max_per_minute=settings.PORTAL_MAX_POR_MINUTO,
        max_wait_seconds=settings.PORTAL_MAX_ESPERA_SEGUNDOS,
        cycle_metrics=metrics,
        daily_store=_PORTAL_DAILY_USAGE,
    )


def _before_portal_call() -> None:
    acquire_portal_call()


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
    *,
    portal_request: bool = True,
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
            if portal_request:
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
