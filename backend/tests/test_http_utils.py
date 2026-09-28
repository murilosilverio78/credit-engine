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


@pytest.fixture(autouse=True)
def reset_portal_rate_limiter(monkeypatch):
    monkeypatch.setattr(http_utils.settings, "PORTAL_MIN_INTERVAL_SECONDS", 0.0)
    http_utils._PORTAL_RATE_LIMITER.reset()
    yield
    http_utils._PORTAL_RATE_LIMITER.reset()


def _response(content: bytes, request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"content-type": "application/json"},
        request=request,
    )


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
