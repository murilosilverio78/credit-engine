import base64
import json
import os
import time


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.integrations.broadfactor.client import BroadfactorClient, Outcome  # noqa: E402


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
    session = FakeSession([FakeResponse(payload=None, content=b"")])
    client._s = session
    client._token = "token"
    client._expira_em = time.time() + 60

    result = client._req("GET", "/cotacoes")

    assert session.calls[0][1] == "http://broadfactor.test/integracao/cotacoes"
    assert result.outcome is Outcome.EMPTY
    assert result.ok is False


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
    refreshed_token = _jwt({"tenant": {"companyName": "Credora"}})
    client = BroadfactorClient("client", "secret", "http://broadfactor.test")
    session = FakeSession(
        [
            FakeResponse(status_code=401, payload={"error": "Unauthorized"}),
            FakeResponse(
                payload={"token": refreshed_token, "expiracaoMs": 600_000}
            ),
            FakeResponse(payload={"items": [1]}),
        ]
    )
    client._s = session
    client._token = "expired-token"
    client._expira_em = time.time() + 60

    result = client._req("GET", "/cotacoes")

    assert result.outcome is Outcome.OK
    assert session.calls[1][2] == ("client", "secret")
    assert session.calls[2][4] == {"Authorization": f"Bearer {refreshed_token}"}


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
