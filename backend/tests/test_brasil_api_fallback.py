from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.cliente_result_classifier import classificar
from app.workers.tasks import brasil_api


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "cnpja_46276066000100.json").read_text(encoding="utf-8"))


class Response:
    def __init__(self, status_code=200, payload=None, headers=None, error=None):
        self.status_code, self.payload, self.headers, self.error = status_code, payload or {}, headers or {}, error

    def raise_for_status(self):
        if self.error:
            raise self.error
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def _client(responses):
    class Client:
        def __init__(self, *_args, **_kwargs):
            self.responses = responses
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, _url): return self.responses.pop(0)
    return Client


def _reset(monkeypatch):
    monkeypatch.setattr(brasil_api, "_cnpja_last_call", 0.0)
    monkeypatch.setattr(brasil_api, "_monotonic", lambda: 100.0)
    monkeypatch.setattr(brasil_api, "_sleep", lambda _seconds: None)


def test_fetch_brasil_api_success_has_source(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(brasil_api.httpx, "Client", _client([Response(payload={"cnpj": "123", "razao_social": "Empresa"})]))
    assert brasil_api._fetch("123")["fonte"] == "BRASIL_API"


def test_fetch_falls_back_to_cnpja_and_normalizes_real_fixture(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(brasil_api.httpx, "Client", _client([Response(500), Response(payload=FIXTURE)]))
    result = brasil_api._fetch("46.276.066/0001-00")
    assert result == {
        "cnpj": "46276066000100", "razao_social": "FACILITA PRESTADORA DE SERVICOS LTDA",
        "nome_fantasia": "Facilita Prestadora de Servicos", "situacao_cadastral": "ATIVA",
        "data_situacao": "2026-06-11", "data_abertura": "2022-05-05",
        "natureza_juridica": "Sociedade Empresária Limitada", "porte": "MICRO EMPRESA",
        "capital_social": 50000, "atividade_principal": "Obras de alvenaria", "cnae_fiscal": "4399103",
        "atividades_secundarias": ["Serviços de pintura de edifícios em geral", "Transporte rodoviário de carga, exceto produtos perigosos e mudanças, municipal"],
        "identificador_matriz_filial": 1,
        "qsa": [{"nome": "Silvano do Nascimento Doho", "qualificacao": "Sócio-Administrador", "data_entrada": "2025-08-19"}],
        "municipio": "PEDRA PRETA", "uf": "MT", "email": "silvano.doho@gmail.com", "telefone": "6699858572",
        "opcao_simples": True, "opcao_mei": False, "regime_tributario": [],
        "fonte": "CNPJA_OPEN", "simples_desde": "2025-01-01",
    }


def test_fetch_reports_both_source_failures(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(brasil_api.httpx, "Client", _client([Response(500), Response(503)]))
    with pytest.raises(RuntimeError, match="BrasilAPI: HTTP 500; CNPJá Open: HTTP 503"):
        brasil_api._fetch("123")


def test_cnpja_429_waits_retry_after_once(monkeypatch):
    _reset(monkeypatch)
    waits = []
    monkeypatch.setattr(brasil_api, "_sleep", waits.append)
    monkeypatch.setattr(brasil_api.httpx, "Client", _client([Response(500), Response(429, headers={"Retry-After": "90"}), Response(payload=FIXTURE)]))
    assert brasil_api._fetch("46276066000100")["fonte"] == "CNPJA_OPEN"
    assert waits == [30.0, 13.0]


def test_cnpja_throttle_waits_between_calls(monkeypatch):
    values = iter([100.0, 100.0, 101.0, 101.0])
    waits = []
    monkeypatch.setattr(brasil_api, "_cnpja_last_call", 0.0)
    monkeypatch.setattr(brasil_api, "_monotonic", lambda: next(values))
    monkeypatch.setattr(brasil_api, "_sleep", waits.append)
    client = _client([Response(payload=FIXTURE), Response(payload=FIXTURE)])()
    brasil_api._cnpja_get(client, "1")
    brasil_api._cnpja_get(client, "2")
    assert waits == [12.0]


def test_classifier_uses_actual_cadastro_source():
    assert classificar("brasil_api", {"cnpj": "1", "fonte": "CNPJA_OPEN"}, cotacao_id=None).fonte == "CNPJA_OPEN"
