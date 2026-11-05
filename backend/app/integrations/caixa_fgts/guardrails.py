"""FGTS-specific namespacing for the conservative shared guardrails."""

from __future__ import annotations

from pathlib import Path

from app.integrations.tst_cndt.guardrails import GuardrailError, LocalGuardrails, mask_cnpj


class FgtsGuardrails(LocalGuardrails):
    """Five requests/minute, one CNPJ at a time, with isolated state files."""

    def __init__(self, state_dir: Path, *, max_attempts_per_minute: int = 5) -> None:
        super().__init__(
            state_dir,
            max_attempts_per_minute=max_attempts_per_minute,
            namespace="caixa-fgts",
        )


__all__ = ("FgtsGuardrails", "GuardrailError", "mask_cnpj")
