import asyncio
import os
from types import SimpleNamespace

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.api.v1.endpoints import eligibility  # noqa: E402


PARAMETERS = [
    {
        "key": "ticket_minimo",
        "value": 10_000,
        "label": "Ticket minimo",
        "unit": "BRL",
        "grupo": "elegibilidade",
    },
    {
        "key": "ticket_maximo",
        "value": 5_000_000,
        "label": "Ticket maximo",
        "unit": "BRL",
        "grupo": "elegibilidade",
    },
    {
        "key": "pct_max_contrato",
        "value": 0.5,
        "label": "Percentual maximo do valor do contrato",
        "unit": "decimal",
        "grupo": "elegibilidade",
    },
    {
        "key": "prazo_padrao_meses",
        "value": 12,
        "label": "Prazo padrao",
        "unit": "meses",
        "grupo": "elegibilidade",
    },
    {
        "key": "dias_minimos_expiracao",
        "value": 5,
        "label": "Dias minimos ate expiracao",
        "unit": "dias",
        "grupo": "elegibilidade",
    },
    {
        "key": "prazo_minimo_dias",
        "value": 60,
        "label": "Prazo minimo",
        "unit": "dias",
        "grupo": "elegibilidade",
    },
    {
        "key": "cnpj_idade_minima_meses",
        "value": 12,
        "label": "Idade minima do CNPJ",
        "unit": "meses",
        "grupo": "elegibilidade",
    },
    {
        "key": "watchdog_heartbeat_timeout_minutos",
        "value": 15,
        "label": "Timeout de heartbeat do watchdog",
        "unit": "minutos",
        "grupo": "operacional",
    },
]


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.action = "select"
        self.changes = {}
        self.filters = []

    def select(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def update(self, changes):
        self.action = "update"
        self.changes = changes
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def execute(self):
        rows = self.db.tables[self.table]
        selected = [
            row
            for row in rows
            if all(row.get(column) == value for column, value in self.filters)
        ]
        if self.action == "update":
            for row in selected:
                row.update(self.changes)
        return SimpleNamespace(data=[dict(row) for row in selected])


class FakeSupabase:
    def __init__(self):
        self.tables = {
            "eligibility_parameters": [dict(row) for row in PARAMETERS],
        }

    def table(self, table):
        return FakeQuery(self, table)


class FakeAudit:
    def __init__(self):
        self.entries = []

    def log(self, **entry):
        self.entries.append(entry)


def make_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/api/v1/elegibilidade/parameters/ticket_minimo",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


def parameter_map():
    return {row["key"]: dict(row) for row in PARAMETERS}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ticket_minimo", 0),
        ("ticket_minimo", 5_000_000),
        ("ticket_maximo", 10_000),
        ("pct_max_contrato", 0.009),
        ("pct_max_contrato", 1.01),
        ("prazo_padrao_meses", 0),
        ("prazo_padrao_meses", 61),
        ("dias_minimos_expiracao", -1),
        ("dias_minimos_expiracao", 91),
        ("prazo_minimo_dias", -1),
        ("prazo_minimo_dias", 366),
        ("cnpj_idade_minima_meses", -1),
        ("cnpj_idade_minima_meses", 121),
        ("watchdog_heartbeat_timeout_minutos", 4),
        ("watchdog_heartbeat_timeout_minutos", 121),
        ("pct_max_contrato", float("nan")),
    ],
)
def test_parameter_range_validation_rejects_invalid_values(key, value):
    with pytest.raises(HTTPException) as exc_info:
        eligibility._validate_value(key, value, parameter_map())

    assert exc_info.value.status_code == 422


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ticket_minimo", 1),
        ("ticket_maximo", 10_001),
        ("pct_max_contrato", 0.01),
        ("pct_max_contrato", 1),
        ("prazo_padrao_meses", 60),
        ("dias_minimos_expiracao", 0),
        ("prazo_minimo_dias", 365),
        ("cnpj_idade_minima_meses", 120),
        ("watchdog_heartbeat_timeout_minutos", 5),
    ],
)
def test_parameter_range_validation_accepts_boundaries(key, value):
    eligibility._validate_value(key, value, parameter_map())


def test_patch_requires_director(monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(eligibility, "supabase", db)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            eligibility.update_eligibility_parameter(
                "ticket_minimo",
                eligibility.EligibilityParameterUpdate(
                    value=20_000,
                    justificativa="Ajuste de politica",
                ),
                make_request(),
                {"id": "analyst-id", "role": "analista"},
            )
        )

    assert exc_info.value.status_code == 403
    assert db.tables["eligibility_parameters"][0]["value"] == 10_000


def test_patch_updates_audits_and_invalidates_caches(monkeypatch):
    db = FakeSupabase()
    audit = FakeAudit()
    invalidations = []
    monkeypatch.setattr(eligibility, "supabase", db)
    monkeypatch.setattr(eligibility, "audit", audit)
    monkeypatch.setattr(
        eligibility,
        "invalidate_cache",
        lambda: invalidations.append("eligibility"),
    )
    monkeypatch.setattr(
        eligibility,
        "invalidate_watchdog_config_cache",
        lambda: invalidations.append("watchdog"),
    )

    result = asyncio.run(
        eligibility.update_eligibility_parameter(
            "ticket_minimo",
            eligibility.EligibilityParameterUpdate(
                value=20_000,
                justificativa="Calibracao inicial do funil",
            ),
            make_request(),
            {"id": "director-id", "role": "diretor"},
        )
    )

    assert result["value"] == 20_000
    assert result["updated_by"] == "director-id"
    assert result["updated_at"]
    assert invalidations == ["eligibility", "watchdog"]
    assert len(audit.entries) == 1
    assert audit.entries[0]["actor_id"] == "director-id"
    assert audit.entries[0]["actor_type"] == "diretor"
    assert audit.entries[0]["previous_value"]["value"] == 10_000
    assert audit.entries[0]["new_value"]["value"] == 20_000
    assert audit.entries[0]["payload"] == {
        "table": "eligibility_parameters",
        "key": "ticket_minimo",
    }


def test_patch_validates_ticket_against_current_related_value(monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(eligibility, "supabase", db)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            eligibility.update_eligibility_parameter(
                "ticket_maximo",
                eligibility.EligibilityParameterUpdate(
                    value=9_000,
                    justificativa="Ajuste de politica",
                ),
                make_request(),
                {"id": "director-id", "role": "diretor"},
            )
        )

    assert exc_info.value.status_code == 422
    assert db.tables["eligibility_parameters"][1]["value"] == 5_000_000
