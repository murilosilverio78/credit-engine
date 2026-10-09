import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.workers.tasks import contratos_pncp as pncp


FIXTURE = Path(__file__).parent / "fixtures" / "pncp_46276066000100.json"


def fixture_payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def response(status_code, payload):
    request = httpx.Request("GET", "https://pncp.test/api/search/")
    return httpx.Response(status_code, json=payload, request=request)


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def get(self, url, params, headers=None):
        self.calls.append((url, params, headers))
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


def test_normaliza_fixture_real_e_identifica_dedicacao_exclusiva():
    contracts = [pncp._normalize(item) for item in fixture_payload()["items"]]

    assert contracts[0]["unidade_codigo"] == "200123"
    assert contracts[0]["dedicacao_exclusiva"] is True
    assert (
        pncp._normalize({"description": "Cessão de mão de obra"})["dedicacao_exclusiva"]
        is True
    )
    assert (
        pncp._normalize({"description": "Objeto comum"})["dedicacao_exclusiva"] is False
    )


def test_descarta_fornecedor_diferente_e_cancelado(monkeypatch):
    payload = fixture_payload()
    foreign = dict(payload["items"][0], fornecedor_ni="99999999000199")
    canceled = dict(payload["items"][1], cancelado=True)
    payload["items"] += [foreign, canceled]
    payload["total"] = 4
    client = FakeClient([response(200, payload)])
    monkeypatch.setattr(pncp.httpx, "Client", lambda **_: client)

    result = pncp._fetch("46276066000100")

    assert result["n_contratos"] == 2
    assert {
        item["numero_contrato_empenho"] for item in result["contratos_detalhe"]
    } == {"00005", "00006"}


def test_envia_token_do_proxy_pncp_quando_configurado(monkeypatch):
    client = FakeClient([response(200, {"total": 0, "items": []})])
    monkeypatch.setattr(pncp.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(pncp.settings, "PNCP_PROXY_TOKEN", "proxy-secret")

    pncp._fetch("46276066000100")

    assert client.calls[0][2] == {"X-Proxy-Token": "proxy-secret"}


def test_paginated_search_retries_5xx_and_obeys_pages(monkeypatch):
    first = fixture_payload()
    first["items"] = [first["items"][0]]
    first["total"] = 21
    second = fixture_payload()
    second["items"] = [second["items"][1]]
    second["total"] = 21
    client = FakeClient(
        [response(500, {}), response(200, first), response(200, second)]
    )
    pauses = []
    monkeypatch.setattr(pncp.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(pncp.time, "sleep", pauses.append)

    result = pncp._fetch("46276066000100")

    assert result["n_contratos"] == 2
    assert [call[1]["pagina"] for call in client.calls] == [1, 1, 2]
    assert pauses == [2, 1]


def test_empty_response_is_completed_zero_result(monkeypatch):
    client = FakeClient([response(200, {"total": 0, "items": []})])
    monkeypatch.setattr(pncp.httpx, "Client", lambda **_: client)

    result = pncp._fetch("46276066000100")

    assert result["n_contratos"] == 0
    assert result["n_vigentes"] == 0
    assert result["contratos_detalhe"] == []


def test_source_failure_exhausts_retries(monkeypatch):
    client = FakeClient([httpx.TimeoutException("indisponivel")] * 3)
    monkeypatch.setattr(pncp.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(pncp.time, "sleep", lambda _: None)

    with pytest.raises(httpx.TimeoutException):
        pncp._fetch("46276066000100")


def test_matches_contrato_cedido_por_numero_ano_e_desempata_por_valor():
    contracts = [pncp._normalize(item) for item in fixture_payload()["items"]]
    duplicate = dict(contracts[0], valor_global=300000.0)
    contracts.append(duplicate)

    cedido, match = pncp._cedido(contracts, "000062026", 295000.0)

    assert match == "APROXIMADO"
    assert cedido["unidade_codigo"] == "200123"
    assert cedido["numero_contrato_empenho"] == "00006"


def test_hhi_agrega_contratos_por_orgao():
    contracts = [
        {
            "orgao_cnpj": "A",
            "valor_global": 100,
            "data_inicio_vigencia": "2026-01-01",
            "data_fim_vigencia": "2027-01-01",
            "data_assinatura": "2026-01-01",
            "esfera_id": "F",
        },
        {
            "orgao_cnpj": "A",
            "valor_global": 100,
            "data_inicio_vigencia": "2026-01-01",
            "data_fim_vigencia": "2027-01-01",
            "data_assinatura": "2026-01-01",
            "esfera_id": "F",
        },
        {
            "orgao_cnpj": "B",
            "valor_global": 200,
            "data_inicio_vigencia": "2026-01-01",
            "data_fim_vigencia": "2027-01-01",
            "data_assinatura": "2026-01-01",
            "esfera_id": "F",
        },
    ]

    result = pncp._aggregate(contracts, date(2026, 6, 1))

    assert result["hhi"] == pytest.approx(5000, abs=1)


def test_cache_hit_associa_contrato_cedido_a_cada_operacao(monkeypatch):
    from app.core import database

    wallet = {
        "n_contratos": 2,
        "contratos_detalhe": [
            pncp._normalize(item) for item in fixture_payload()["items"]
        ],
    }
    operations = [
        {
            "id": "op-1",
            "contrato_id": "000062026",
            "cotacao_id": None,
            "valor_global_contrato": None,
            "saldo_vincendo": 280200,
        },
        {
            "id": "op-2",
            "contrato_id": "000052026",
            "cotacao_id": None,
            "valor_global_contrato": None,
            "saldo_vincendo": 120000,
        },
    ]
    snapshots = [
        {
            "operation_id": operation["id"],
            "component": "contratos_pncp",
            "status": "completed",
            "parsed_result": dict(wallet),
        }
        for operation in operations
    ]

    class Query:
        def __init__(self, rows):
            self.rows = rows
            self.filters = []
            self.single = False
            self.payload = None

        def select(self, *_args):
            return self

        def eq(self, key, value):
            self.filters.append((key, value))
            return self

        def maybe_single(self):
            self.single = True
            return self

        def update(self, payload):
            self.payload = payload
            return self

        def upsert(self, payload, **_kwargs):
            for row in self.rows:
                if (
                    row.get("operation_id") == payload.get("operation_id")
                    and row.get("component") == payload.get("component")
                ):
                    return self
            self.rows.append(payload.copy())
            return self

        def execute(self):
            rows = [
                row for row in self.rows
                if all(row.get(key) == value for key, value in self.filters)
            ]
            if self.payload:
                for row in rows:
                    row.update(self.payload)
            data = rows[0].copy() if self.single and rows else None
            return SimpleNamespace(data=data)

    class Database:
        def table(self, name):
            return Query(operations if name == "operations" else snapshots)

    db = Database()

    class CachedTask:
        def execute(self, *_args, **_kwargs):
            return {"status": "completed", "cached": True}

    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(pncp, "_task", CachedTask())

    pncp.run_contratos_pncp("op-1")
    pncp.run_contratos_pncp("op-2")

    first, second = snapshots
    assert first["parsed_result"]["contrato_cedido"]["numero_contrato_empenho"] == "00006"
    assert second["parsed_result"]["contrato_cedido"]["numero_contrato_empenho"] == "00005"
    assert first["parsed_result"]["contrato_cedido_match"] == "EXATO"
    assert second["parsed_result"]["contrato_cedido_match"] == "EXATO"
    assert operations[0]["uasg"] == "200123"
    assert operations[1]["uasg"] == "170607"


def test_run_cria_snapshot_ausente_e_nao_associa_quando_falha(monkeypatch):
    class Query:
        def __init__(self, rows):
            self.rows = rows

        def upsert(self, payload, **_kwargs):
            if not self.rows:
                self.rows.append(payload.copy())
            return self

        def execute(self):
            return SimpleNamespace(data=self.rows)

    snapshots = []

    class Database:
        def table(self, _name):
            return Query(snapshots)

    class FailedTask:
        def execute(self, *_args, **_kwargs):
            return {"status": "failed"}

    from app.core import database

    monkeypatch.setattr(database, "supabase", Database())
    monkeypatch.setattr(pncp, "_task", FailedTask())

    pncp.run_contratos_pncp("op-sem-snapshot")

    assert snapshots == [{
        "operation_id": "op-sem-snapshot",
        "component": "contratos_pncp",
        "status": "pending",
    }]
