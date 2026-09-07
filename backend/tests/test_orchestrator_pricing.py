import asyncio
import os
from types import SimpleNamespace

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.workers.tasks import orchestrator  # noqa: E402


class PricingQuery:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.action = None
        self.payload = None

    def select(self, *_args, **_kwargs):
        self.action = "select"
        return self

    def update(self, payload):
        self.action = "update"
        self.payload = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def maybe_single(self):
        self.database.used_maybe_single = True
        return self

    def execute(self):
        if self.action == "select" and self.table == "component_snapshots":
            if not self.database.snapshot_exists:
                return SimpleNamespace(data=None)
            return SimpleNamespace(data={"parsed_result": self.database.score_result})
        if self.action == "select" and self.table == "operations":
            return SimpleNamespace(data=self.database.operation)
        if self.action == "update" and self.table == "operations":
            self.database.completed_payload = self.payload
            return SimpleNamespace(data=[{"id": "op-1"}])
        return SimpleNamespace(data=[])


class PricingSupabase:
    def __init__(self, operation):
        self.operation = operation
        self.score_result = {
            "score": 72,
            "rating": "C",
            "limite_aprovado_rs": 400_000,
            "ajuste_pd": {"multiplicador_volatilidade": 1.08},
        }
        self.completed_payload = None
        self.snapshot_exists = True
        self.used_maybe_single = False

    def table(self, name):
        return PricingQuery(self, name)


@pytest.mark.parametrize(
    ("operation", "expected_value", "expected_term"),
    [
        (
            {
                "valor_solicitado": 500_000,
                "valor_enquadrado": 400_000,
                "prazo_dias": None,
                "prazo_final_meses": 12,
            },
            400_000,
            12,
        ),
        (
            {
                "valor_solicitado": 500_000,
                "valor_enquadrado": None,
                "prazo_dias": 180,
                "prazo_final_meses": None,
            },
            500_000,
            6,
        ),
    ],
)
def test_completion_prices_effective_fields_with_legacy_fallback(
    monkeypatch,
    operation,
    expected_value,
    expected_term,
):
    database = PricingSupabase(operation)
    calls = []

    def fake_compute_taxa(rating, valor, prazo_meses, pd_multiplier=1.0):
        calls.append((rating, valor, prazo_meses, pd_multiplier))
        return {"taxa_sugerida_am": 0.025}

    monkeypatch.setattr(orchestrator, "supabase", database)
    monkeypatch.setattr(
        "app.services.pricing_engine.compute_taxa",
        fake_compute_taxa,
    )

    asyncio.run(orchestrator._complete_analysis("op-1"))

    assert calls == [("C", expected_value, expected_term, 1.08)]
    assert database.completed_payload["taxa_sugerida"] == 0.025


def test_completion_without_score_snapshot_is_recoverable(monkeypatch):
    database = PricingSupabase({})
    database.snapshot_exists = False
    monkeypatch.setattr(orchestrator, "supabase", database)

    result = asyncio.run(orchestrator._complete_analysis("op-1"))

    assert database.used_maybe_single is True
    assert result == {
        "operation_id": "op-1",
        "status": "completed",
        "score_available": False,
        "reason": "score_engine snapshot ausente; operacao concluida sem score",
    }
    assert database.completed_payload["status"] == "completed"
    assert database.completed_payload["score"] is None
    assert database.completed_payload["rating"] is None
    assert database.completed_payload["taxa_sugerida"] is None
    assert database.completed_payload["pricing_skipped_reason"] == result["reason"]
