from __future__ import annotations

import os


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.workers.http_utils import PortalRespostaVaziaError  # noqa: E402
from app.workers.tasks import pessoa_juridica  # noqa: E402


def test_persistent_empty_response_remains_sem_registro(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(pessoa_juridica.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        pessoa_juridica,
        "fetch_json_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PortalRespostaVaziaError("persistente")
        ),
    )

    result = pessoa_juridica._fetch("03.012.610/0001-01", token="test")

    assert result["cnpj"] == "03012610000101"
    assert result["erro"] == "sem_registro"
    assert result["possui_sancao"] is False
