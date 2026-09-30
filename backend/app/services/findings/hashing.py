"""Stable fingerprints for idempotent finding runs."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def entrada_hash(snapshot: Any) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
