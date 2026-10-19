"""Parsing rules used for data persisted from Comprasnet."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any


def parse_number(value: Any) -> float | None:
    """Parse Comprasnet numeric values without corrupting decimal-dot inputs."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip().replace("R$", "").replace(" ", "")
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_data(value: Any) -> date | None:
    """Parse the ISO and Brazilian date formats returned by Comprasnet."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if "/" in text:
            for pattern in ("%d/%m/%Y", "%d/%m/%y"):
                try:
                    return datetime.strptime(text, pattern).date()
                except ValueError:
                    continue
            return None
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None
