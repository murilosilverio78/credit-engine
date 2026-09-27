import base64
import concurrent.futures
import json
import os
import threading
import time

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.integrations.broadfactor.client import (  # noqa: E402
    _BROADFACTOR_TOKEN_CACHE,
    BroadfactorClient,
    BroadfactorError,
    Outcome,
    QuotationInactiveError,
    Result,
)


@pytest.fixture(autouse=True)
def reset_shared_token_cache():
    _BROADFACTOR_TOKEN_CACHE.clear()
    yield
    _BROADFACTOR_TOKEN_CACHE.clear()


def _jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"content-type": "application/json"}
        self.content = (
            json.dumps(payload).encode() if content is None and payload is not None else content
        ) or b""

    def json(self):
        if self._payload is None:
            raise ValueError("empty response")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, auth, timeout):
        self.calls.append(("POST", url, auth, timeout, None))
        return next(self.responses)

    def request(self, method, url, json, timeout, headers):
        self.calls.append((method, url, None, timeout, headers))
        return next(self.responses)


def test_auth_uses_basic_and_expiration_in_milliseconds():
    client = BroadfactorClient("client", "secret", "http://broadfactor.test")
    session = FakeSession(
        [
            FakeResponse(
                payload={
                    "token": _jwt({"tenant": {"companyName": "Credora"}}),
                    "expiracaoMs": 600_000,
                }
            )
        ]
    )
    client._s = session
    before = time.time()

    client._autenticar()

    assert session.calls[0][1].endswith("/integracao/autenticar/token")
    assert session.calls[0][2] == ("client", "secret")
    assert before + 479 <= client._expira_em <= before + 481


def test_request_forces_prefix_and_distinguishes_empty_200():
    client = BroadfactorClient("client", "secret", "http://broadfactor.test")
    session = FakeSession(
        [
            FakeResponse(payload={"token": _jwt({}), "expiracaoMs": 600_000}),
            FakeResponse(payload=None, content=b""),
        ]
    )
    client._s = session

    result = client._req("GET", "/cotacoes")

    assert session.calls[1][1] == "http://broadfactor.test/integracao/cotacoes"
    assert result.outcome is Outcome.EMPTY
    assert result.ok is False


def test_listar_cotacoes_raises_on_transport_error(monkeypatch):
    client = BroadfactorClient("client", "secret", "http://broadfactor.test")
    monkeypatch.setattr(
        client,
        "_req",
        lambda *_args, **_kwargs: Result(
            Outcome.ERROR,
            status=500,
            message="upstream failed",
            endpoint="/integracao/cotacoes",
        ),
    )

    with pytest.raises(BroadfactorError, match="upstream failed"):
        client.listar_cotacoes()


def test_documents_raise_specific_error_for_inactive_quote(monkeypatch):
    client = BroadfactorClient("client", "secret", "http://broadfactor.test")
    monkeypatch.setattr(
        client,
        "_req",
        lambda *_args, **_kwargs: Result(
            Outcome.ERROR,
            status=409,
            message="QUOTATION_INACTIVE",
            endpoint="/integracao/empresa/C-1/documentos",
        ),
    )

    with pytest.raises(QuotationInactiveError, match="QUOTATION_INACTIVE"):
        client.documentos_da_cotacao("C-1")


def test_inactive_quote_message_is_read_from_serpro_envelope():
    client = BroadfactorClient("client", "secret", "http://broadfactor.test")

    result = client._interpretar(
        FakeResponse(
            status_code=409,
            payload={"serproMessage": "QUOTATION_INACTIVE"},
        ),
        "/integracao/empresa/C-1/documentos",
    )

    assert result.message == "QUOTATION_INACTIVE"


def test_default_credentials_come_from_settings(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.broadfactor.client.settings.BROADFACTOR_CLIENT_ID",
        "configured-client",
    )
    monkeypatch.setattr(
        "app.integrations.broadfactor.client.settings.BROADFACTOR_CLIENT_SECRET",
        "configured-secret",
    )
    monkeypatch.setattr(
        "app.integrations.broadfactor.client.settings.BROADFACTOR_BASE_URL",
        "http://configured.test",
    )

    client = BroadfactorClient()

    assert client.client_id == "configured-client"
    assert client.client_secret == "configured-secret"
    assert client.base_url == "http://configured.test"


def test_httpx_client_respects_ssl_setting(monkeypatch):
    configured = {}

    class FakeHttpxClient:
        def __init__(self, **kwargs):
            configured.update(kwargs)

    monkeypatch.setattr(
        "app.integrations.broadfactor.client.settings.HTTPX_VERIFY_SSL",
        False,
    )
    monkeypatch.setattr(
        "app.integrations.broadfactor.client.httpx.Client",
        FakeHttpxClient,
    )

    BroadfactorClient("client", "secret")

    assert configured == {"timeout": 30, "verify": False}


def test_401_reauthenticates_and_retries_request():
    initial_token = _jwt({"version": 1})
    refreshed_token = _jwt({"version": 2})
    client = BroadfactorClient("client", "secret", "http://broadfactor.test")
    session = FakeSession(
        [
            FakeResponse(
                payload={"token": initial_token, "expiracaoMs": 600_000}
            ),
            FakeResponse(status_code=401, payload={"error": "Unauthorized"}),
            FakeResponse(
                payload={"token": refreshed_token, "expiracaoMs": 600_000}
            ),
            FakeResponse(payload={"items": [1]}),
        ]
    )
    client._s = session

    result = client._req("GET", "/cotacoes")

    assert result.outcome is Outcome.OK
    assert session.calls[2][2] == ("client", "secret")
    assert session.calls[3][4] == {"Authorization": f"Bearer {refreshed_token}"}


def test_distinct_clients_share_the_same_token():
    token = _jwt({"tenant": {"companyName": "Credora"}})
    first = BroadfactorClient("client", "secret", "http://broadfactor.test")
    second = BroadfactorClient("client", "secret", "http://broadfactor.test")
    first_session = FakeSession(
        [FakeResponse(payload={"token": token, "expiracaoMs": 600_000})]
    )
    second_session = FakeSession([])
    first._s = first_session
    second._s = second_session

    first._garantir_token()
    second._garantir_token()

    assert first._token == second._token == token
    assert len(first_session.calls) == 1
    assert second_session.calls == []


def test_expiration_causes_one_authentication_with_concurrent_threads(monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "app.integrations.broadfactor.client.time.time",
        lambda: clock["now"],
    )
    auth_calls = []
    auth_lock = threading.Lock()
    initial_token = _jwt({"version": 1})
    refreshed_token = _jwt({"version": 2})

    class SharedAuthSession:
        def post(self, url, auth, timeout):
            with auth_lock:
                auth_calls.append(url)
                token = initial_token if len(auth_calls) == 1 else refreshed_token
            return FakeResponse(payload={"token": token, "expiracaoMs": 121_000})

    session = SharedAuthSession()
    seed = BroadfactorClient("client", "secret", "http://broadfactor.test")
    seed._s = session
    seed._garantir_token()
    clock["now"] = 1_002.0
    clients = [
        BroadfactorClient("client", "secret", "http://broadfactor.test")
        for _ in range(8)
    ]
    for client in clients:
        client._s = session

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda client: client._garantir_token(), clients))

    assert len(auth_calls) == 2
    assert all(client._token == refreshed_token for client in clients)


def test_401_invalidates_shared_token_for_all_clients():
    initial_token = _jwt({"version": 1})
    refreshed_token = _jwt({"version": 2})
    first = BroadfactorClient("client", "secret", "http://broadfactor.test")
    second = BroadfactorClient("client", "secret", "http://broadfactor.test")
    first._s = FakeSession(
        [
            FakeResponse(payload={"token": initial_token, "expiracaoMs": 600_000}),
            FakeResponse(status_code=401, payload={"error": "Unauthorized"}),
            FakeResponse(payload={"token": refreshed_token, "expiracaoMs": 600_000}),
            FakeResponse(payload={"items": [1]}),
        ]
    )
    second._s = FakeSession([])
    first._garantir_token()
    second._garantir_token()

    result = first._req("GET", "/cotacoes")
    second._garantir_token()

    assert result.outcome is Outcome.OK
    assert first._token == second._token == refreshed_token
    assert second._s.calls == []


def test_404_distinguishes_missing_route_from_business_not_found():
    client = BroadfactorClient("client", "secret", "http://broadfactor.test")

    missing_route = client._interpretar(
        FakeResponse(
            status_code=404,
            payload={"error": "No static resource integracao/fantasma"},
        ),
        "/integracao/fantasma",
    )
    business_not_found = client._interpretar(
        FakeResponse(
            status_code=404,
            payload={"customMessage": "THERE_IS_NO_FILE_YET"},
        ),
        "/integracao/cotacoes/C-1/contratos",
    )

    assert missing_route.outcome is Outcome.NO_ROUTE
    assert business_not_found.outcome is Outcome.NOT_FOUND


def test_receipts_strict_mode_rejects_partial_page_failure(monkeypatch):
    client = BroadfactorClient("client", "secret", "http://broadfactor.test")
    responses = iter(
        [
            Result(
                Outcome.OK,
                data={
                    "content": [
                        {
                            "value": 100,
                            "nameOrganization": "Orgao A",
                            "competency": "01/2025",
                        }
                    ],
                    "totalPages": 2,
                },
            ),
            Result(Outcome.ERROR, message="network error"),
        ]
    )
    monkeypatch.setattr(client, "_req", lambda *args, **kwargs: next(responses))

    with pytest.raises(BroadfactorError):
        client.recebimentos("C-1", paginas=10, exigir_completo=True)
