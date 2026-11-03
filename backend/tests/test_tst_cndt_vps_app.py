from __future__ import annotations

from app.integrations.tst_cndt.vps_app import _authorized


def test_vps_proxy_authentication_requires_exact_nonempty_token(monkeypatch):
    monkeypatch.setenv("CNDT_PROXY_TOKEN", "shared-secret")
    assert _authorized("shared-secret") is True
    assert _authorized("wrong-secret") is False
    assert _authorized(None) is False


def test_vps_proxy_authentication_rejects_unconfigured_token(monkeypatch):
    monkeypatch.delenv("CNDT_PROXY_TOKEN", raising=False)
    assert _authorized("anything") is False
