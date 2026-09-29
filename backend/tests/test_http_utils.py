from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Lock
import time

import httpx
import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.workers import http_utils  # noqa: E402


class FakeDailyUsage:
    def __init__(self, consumed=0):
        self.consumed = consumed
        self.alerted = False

    def claim(self, limit):
        if self.consumed >= limit:
            return http_utils.DailyClaim(False, self.consumed, usage_date="2026-09-28")
        self.consumed += 1
        alert_now = not self.alerted and self.consumed >= int(limit * 0.7 + 0.999)
        self.alerted = self.alerted or alert_now
        return http_utils.DailyClaim(
            True,
            self.consumed,
            alert_now=alert_now,
            usage_date="2026-09-28",
        )

    def current(self):
        return http_utils.DailyClaim(True, self.consumed, usage_date="2026-09-28")


@pytest.fixture(autouse=True)
def reset_portal_rate_limiter(monkeypatch):
    monkeypatch.setattr(http_utils.settings, "PORTAL_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_POR_MINUTO", 10_000)
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_POR_CICLO", 10_000)
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_POR_DIA", 100_000)
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_ESPERA_SEGUNDOS", 120.0)
    monkeypatch.setattr(http_utils, "_PORTAL_DAILY_USAGE", FakeDailyUsage())
    http_utils._PORTAL_RATE_LIMITER.reset()
    yield
    http_utils._PORTAL_RATE_LIMITER.reset()


def _install_fake_clock(monkeypatch):
    clock = {"now": 0.0, "sleeps": []}

    def sleep(delay):
        clock["sleeps"].append(delay)
        clock["now"] += delay + 0.001

    monkeypatch.setattr(http_utils.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(http_utils.time, "sleep", sleep)
    return clock


def _response(content: bytes, request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"content-type": "application/json"},
        request=request,
    )


def test_portal_helpers_use_configured_base_url_and_proxy_token(monkeypatch):
    monkeypatch.setattr(
        http_utils.settings,
        "PORTAL_BASE_URL",
        "https://proxy.example/",
    )
    monkeypatch.setattr(http_utils.settings, "PORTAL_PROXY_TOKEN", "proxy-secret")

    assert http_utils.portal_api_url("/ceis") == (
        "https://proxy.example/api-de-dados/ceis"
    )
    assert http_utils.portal_headers("portal-key") == {
        "chave-api-dados": "portal-key",
        "X-Proxy-Token": "proxy-secret",
    }


def test_portal_headers_omit_proxy_header_when_token_is_empty(monkeypatch):
    monkeypatch.setattr(http_utils.settings, "PORTAL_PROXY_TOKEN", "")

    assert http_utils.portal_headers("portal-key") == {
        "chave-api-dados": "portal-key",
    }


def test_151st_call_waits_for_sliding_minute_window(monkeypatch):
    clock = _install_fake_clock(monkeypatch)
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_POR_MINUTO", 150)
    metrics = http_utils.PortalCycleMetrics()
    token = http_utils.set_portal_cycle_metrics(metrics)
    try:
        for _ in range(151):
            http_utils.acquire_portal_call()
    finally:
        http_utils.reset_portal_cycle_metrics(token)

    assert len(clock["sleeps"]) == 1
    assert clock["sleeps"][0] == pytest.approx(60.0)
    assert metrics.snapshot()["guardrail_minuto_acionado"] == 1


def test_minute_wait_above_limit_becomes_unavailable(monkeypatch):
    _install_fake_clock(monkeypatch)
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_POR_MINUTO", 150)
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_ESPERA_SEGUNDOS", 10.0)
    for _ in range(150):
        http_utils.acquire_portal_call()

    with pytest.raises(
        http_utils.PortalMinuteWaitExceeded,
        match="minuto_espera_excedida",
    ):
        http_utils.acquire_portal_call()


def test_daily_limit_blocks_new_calls_as_source_unavailable(monkeypatch):
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_POR_DIA", 10_000)
    monkeypatch.setattr(
        http_utils,
        "_PORTAL_DAILY_USAGE",
        FakeDailyUsage(consumed=10_000),
    )
    metrics = http_utils.PortalCycleMetrics()
    token = http_utils.set_portal_cycle_metrics(metrics)
    try:
        with pytest.raises(
            http_utils.PortalDailyLimitExceeded,
            match="dia_excedido",
        ):
            http_utils.acquire_portal_call()
    finally:
        http_utils.reset_portal_cycle_metrics(token)

    assert metrics.snapshot()["guardrail_dia_acionado"] == 1


def test_seventy_percent_alert_is_emitted_once_per_window(monkeypatch):
    warnings = []
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_POR_MINUTO", 10)
    monkeypatch.setattr(
        http_utils.logger,
        "warning",
        lambda event, **values: warnings.append((event, values)),
    )

    for _ in range(9):
        http_utils.acquire_portal_call()

    minute_alerts = [
        values
        for event, values in warnings
        if event == "portal_guardrail.alerta" and values["guardrail"] == "minuto"
    ]
    assert minute_alerts == [{"guardrail": "minuto", "consumo": 7, "limite": 10}]


def test_cycle_and_daily_seventy_percent_alerts_are_each_emitted_once(monkeypatch):
    warnings = []
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_POR_CICLO", 10)
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_POR_DIA", 10)
    monkeypatch.setattr(
        http_utils,
        "_PORTAL_DAILY_USAGE",
        FakeDailyUsage(),
    )
    monkeypatch.setattr(
        http_utils.logger,
        "warning",
        lambda event, **values: warnings.append((event, values)),
    )
    metrics = http_utils.PortalCycleMetrics()
    token = http_utils.set_portal_cycle_metrics(metrics)
    try:
        for _ in range(9):
            http_utils.acquire_portal_call()
    finally:
        http_utils.reset_portal_cycle_metrics(token)

    alerts = [
        values["guardrail"]
        for event, values in warnings
        if event == "portal_guardrail.alerta"
    ]
    assert alerts.count("ciclo") == 1
    assert alerts.count("dia") == 1


def test_cycle_is_exhausted_exactly_at_configured_limit(monkeypatch):
    monkeypatch.setattr(http_utils.settings, "PORTAL_MAX_POR_CICLO", 3)
    metrics = http_utils.PortalCycleMetrics()
    token = http_utils.set_portal_cycle_metrics(metrics)
    try:
        for _ in range(3):
            http_utils.acquire_portal_call()

        assert metrics.is_cycle_exceeded() is True
        assert metrics.snapshot()["guardrail_ciclo_acionado"] == 1
        with pytest.raises(http_utils.PortalCycleLimitExceeded):
            http_utils.acquire_portal_call()
    finally:
        http_utils.reset_portal_cycle_metrics(token)

    assert metrics.snapshot()["guardrail_ciclo_acionado"] == 1


def test_non_portal_request_does_not_consume_guardrails():
    request = httpx.Request("GET", "https://contratos.comprasnet.gov.br/teste")
    metrics = http_utils.PortalCycleMetrics()
    token = http_utils.set_portal_cycle_metrics(metrics)
    try:
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: _response(b'{"ok": true}', request)
            )
        ) as client:
            result = http_utils.fetch_json_with_retry(
                client,
                str(request.url),
                portal_request=False,
            )
    finally:
        http_utils.reset_portal_cycle_metrics(token)

    assert result == {"ok": True}
    assert metrics.snapshot()["consumo_ciclo"] == 0
    assert http_utils._PORTAL_DAILY_USAGE.consumed == 0


def test_non_portal_retry_respects_total_timeout(monkeypatch):
    clock = _install_fake_clock(monkeypatch)
    calls = []
    monkeypatch.setattr(http_utils, "_retry_delay", lambda _attempt: 8.0)

    def unavailable(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(504, request=request)

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as client:
        with pytest.raises(httpx.TimeoutException, match="deadline exceeded"):
            http_utils.fetch_json_with_retry(
                client,
                "https://contratos.comprasnet.gov.br/teste",
                portal_request=False,
                total_timeout_seconds=5,
            )

    assert len(calls) == 1
    assert clock["sleeps"] == [5.0]


def test_empty_body_retries_then_succeeds(monkeypatch):
    request = httpx.Request("GET", "https://portal.test/recurso")
    responses = iter(
        [
            _response(b"", request),
            _response(b'[{"id": 1}]', request),
        ]
    )
    monkeypatch.setattr(http_utils.settings, "PORTAL_EMPTY_BODY_POLICY", "raise")
    monkeypatch.setattr(http_utils.time, "sleep", lambda _delay: None)

    with httpx.Client(transport=httpx.MockTransport(lambda _request: next(responses))) as client:
        result = http_utils.fetch_json_with_retry(
            client,
            "https://portal.test/recurso",
            params={"pagina": 2},
        )

    assert result == [{"id": 1}]


def test_persistent_empty_body_raises_with_raise_policy(monkeypatch):
    monkeypatch.setattr(http_utils.settings, "PORTAL_EMPTY_BODY_POLICY", "raise")
    monkeypatch.setattr(http_utils.time, "sleep", lambda _delay: None)

    def empty(request: httpx.Request) -> httpx.Response:
        return _response(b"", request)

    with httpx.Client(transport=httpx.MockTransport(empty)) as client:
        with pytest.raises(http_utils.PortalRespostaVaziaError):
            http_utils.fetch_json_with_retry(
                client,
                "https://portal.test/recurso?pagina=2",
                max_retries=2,
            )


def test_persistent_empty_body_returns_empty_with_rollback_policy(monkeypatch):
    calls = 0
    monkeypatch.setattr(http_utils.settings, "PORTAL_EMPTY_BODY_POLICY", "empty")

    def empty(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(b"", request)

    with httpx.Client(transport=httpx.MockTransport(empty)) as client:
        result = http_utils.fetch_json_with_retry(
            client,
            "https://portal.test/recurso",
            params={"pagina": 2},
        )

    assert result == []
    assert calls == 1


def test_rate_limiter_spaces_calls_from_different_threads(monkeypatch):
    interval = 0.05
    call_times = []
    call_lock = Lock()
    monkeypatch.setattr(
        http_utils.settings,
        "PORTAL_MIN_INTERVAL_SECONDS",
        interval,
    )

    def respond(request: httpx.Request) -> httpx.Response:
        with call_lock:
            call_times.append(time.monotonic())
        return _response(b"[]", request)

    def fetch() -> None:
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            http_utils.fetch_json_with_retry(
                client,
                "https://portal.test/recurso",
                max_retries=0,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _index: fetch(), range(2)))

    assert len(call_times) == 2
    assert call_times[1] - call_times[0] >= interval * 0.8


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            "429",
            {
                "falhas_429_apos_retries": 1,
                "falhas_504_apos_retries": 0,
                "timeouts_apos_retries": 0,
            },
        ),
        (
            "504",
            {
                "falhas_429_apos_retries": 0,
                "falhas_504_apos_retries": 1,
                "timeouts_apos_retries": 0,
            },
        ),
        (
            "timeout",
            {
                "falhas_429_apos_retries": 0,
                "falhas_504_apos_retries": 0,
                "timeouts_apos_retries": 1,
            },
        ),
    ],
)
def test_cycle_metrics_count_terminal_portal_failures(failure, expected):
    metrics = http_utils.PortalCycleMetrics()
    token = http_utils.set_portal_cycle_metrics(metrics)

    def fail(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(int(failure), request=request)

    try:
        with httpx.Client(transport=httpx.MockTransport(fail)) as client:
            with pytest.raises((httpx.HTTPStatusError, httpx.ReadTimeout)):
                http_utils.fetch_json_with_retry(
                    client,
                    "https://portal.test/recurso",
                    max_retries=0,
                )
    finally:
        http_utils.reset_portal_cycle_metrics(token)

    snapshot = metrics.snapshot()
    assert snapshot["total_chamadas_portal"] == 1
    assert {
        "falhas_429_apos_retries": snapshot["falhas_429_apos_retries"],
        "falhas_504_apos_retries": snapshot["falhas_504_apos_retries"],
        "timeouts_apos_retries": snapshot["timeouts_apos_retries"],
    } == expected


def test_429_suspends_global_limiter_but_504_does_not(monkeypatch):
    suspended = []
    monkeypatch.setattr(
        http_utils._PORTAL_RATE_LIMITER,
        "suspend",
        lambda delay: suspended.append(delay),
    )

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "17"}, request=request)

    with httpx.Client(transport=httpx.MockTransport(rate_limited)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            http_utils.fetch_json_with_retry(
                client,
                "https://portal.test/recurso",
                max_retries=0,
            )

    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, request=request)

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            http_utils.fetch_json_with_retry(
                client,
                "https://portal.test/recurso",
                max_retries=0,
            )

    assert suspended == [17.0]


def test_429_caps_global_cooldown_and_logs_requested_retry_after(monkeypatch):
    suspended = []
    warnings = []
    monkeypatch.setattr(
        http_utils.settings,
        "PORTAL_429_MAX_COOLDOWN_SECONDS",
        120.0,
    )
    monkeypatch.setattr(
        http_utils._PORTAL_RATE_LIMITER,
        "suspend",
        lambda delay: suspended.append(delay),
    )
    monkeypatch.setattr(
        http_utils.logger,
        "warning",
        lambda event, **values: warnings.append((event, values)),
    )

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "600"}, request=request)

    with httpx.Client(transport=httpx.MockTransport(rate_limited)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            http_utils.fetch_json_with_retry(
                client,
                "https://portal.test/recurso",
                max_retries=0,
            )

    assert suspended == [120.0]
    assert warnings == [
        (
            "portal_transparencia.rate_limited",
            {
                "url": "https://portal.test/recurso",
                "status": 429,
                "tentativa": 1,
                "retry_after_header": "600",
                "cooldown_s": 120.0,
            },
        )
    ]


def test_retry_crosses_simulated_instability_window(monkeypatch):
    request_count = 0
    delays = []
    monkeypatch.setattr(http_utils.random, "uniform", lambda lower, upper: 1.0)
    monkeypatch.setattr(http_utils.time, "sleep", lambda delay: delays.append(delay))

    def unstable(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count < 5:
            return httpx.Response(504, request=request)
        return _response(b'{"status": "ok"}', request)

    with httpx.Client(transport=httpx.MockTransport(unstable)) as client:
        result = http_utils.fetch_json_with_retry(
            client,
            "https://portal.test/recurso",
        )

    assert result == {"status": "ok"}
    assert request_count == 5
    assert delays == [2.2, 8.0, 20.0, 40.0]
