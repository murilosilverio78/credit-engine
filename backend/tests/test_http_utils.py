from __future__ import annotations

import os

import httpx
import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.workers import http_utils  # noqa: E402


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
