"""Boundary parity for SQL-seeded numeric bands."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.policy.engine import _band

SQL = Path(__file__).parents[2] / "infra/supabase/migrations/045_politica_v0.sql"


def _seeded(name: str):
    match = re.search(r"\('" + name + r"', '(\[.*?\])'::JSONB\)", SQL.read_text(encoding="utf-8"))
    assert match, name
    return json.loads(match.group(1))


def test_seeded_age_and_capital_boundaries_match_official():
    from app.workers.tasks.score_engine import _capital_score, _idade_score

    cases = (("faixas_idade", _idade_score), ("faixas_capital", _capital_score))
    for name, official in cases:
        bands = _seeded(name)
        for item in bands:
            if "ate" not in item:
                continue
            limit = float(item["ate"])
            for value in (limit - 0.0001, limit, limit + 0.0001):
                assert _band(value, bands) == official(value), (name, value)
    assert _band(20.0, _seeded("faixas_idade")) == 88
    assert _band(2_000_000, _seeded("faixas_capital")) == 88


def test_seed_has_an_explicit_operator_for_every_numeric_threshold():
    for name in ("faixas_idade", "faixas_capital"):
        for item in _seeded(name):
            if "ate" in item:
                assert "inclusivo" in item, (name, item)
