"""File-backed, conservative guard-rails for the standalone VPS process."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Iterator


def cnpj_hash(cnpj: str) -> str:
    return hashlib.sha256(cnpj.encode("ascii")).hexdigest()[:16]


def mask_cnpj(cnpj: str) -> str:
    """Safe identifier for logs; it never contains the full registration."""
    return f"***.***.***/****-{cnpj[-2:]}" if len(cnpj) >= 2 else "***"


class GuardrailError(RuntimeError):
    pass


class LocalGuardrails:
    """Coordinate one shared output directory without a server-side database."""

    def __init__(self, state_dir: Path, *, max_attempts_per_minute: int = 5) -> None:
        self.state_dir = state_dir
        self.max_attempts_per_minute = max_attempts_per_minute
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / ".locks").mkdir(exist_ok=True)

    def _read_json(self, name: str, default: object) -> object:
        try:
            return json.loads((self.state_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _write_json(self, name: str, value: object) -> None:
        target = self.state_dir / name
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    def consume_rate_slot(self, *, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        events = [item for item in self._read_json(".cndt-rate.json", []) if isinstance(item, (int, float)) and item > current - 60]
        if len(events) >= self.max_attempts_per_minute:
            raise GuardrailError("limite_global_de_tentativas")
        events.append(current)
        self._write_json(".cndt-rate.json", events)

    @contextmanager
    def cnpj_lock(self, cnpj: str) -> Iterator[None]:
        lock = self.state_dir / ".locks" / f"{cnpj_hash(cnpj)}.lock"
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise GuardrailError("emissao_concorrente_por_cnpj") from exc
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            yield
        finally:
            os.close(descriptor)
            try:
                lock.unlink()
            except FileNotFoundError:
                pass

    def reusable(self, cnpj: str) -> dict | None:
        cache = self._read_json(".cndt-cache.json", {})
        item = cache.get(cnpj_hash(cnpj)) if isinstance(cache, dict) else None
        if not isinstance(item, dict) or item.get("success_date") != date.today().isoformat():
            return None
        path = Path(str(item.get("storage_key") or ""))
        if path.is_file() and item.get("data_validade"):
            try:
                if datetime.strptime(item["data_validade"], "%d/%m/%Y").date() >= date.today():
                    return item
            except ValueError:
                pass
        return None

    def remember_success(self, cnpj: str, result: dict) -> None:
        cache = self._read_json(".cndt-cache.json", {})
        if not isinstance(cache, dict):
            cache = {}
        cache[cnpj_hash(cnpj)] = {**result, "success_date": date.today().isoformat()}
        self._write_json(".cndt-cache.json", cache)
