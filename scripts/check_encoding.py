"""Fail if backend Python string literals contain likely accent mojibake.

The check targets literal question marks between ASCII letters, for example
"Opera??o" or "n?o", while ignoring comments and non-string tokens.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import sys
import tokenize


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
MOJIBAKE_RE = re.compile(r"[A-Za-z]\?[A-Za-z]")


def _string_value(token_text: str) -> str:
    try:
        value = ast.literal_eval(token_text)
    except Exception:
        return token_text
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    return value if isinstance(value, str) else ""


def main() -> int:
    failures: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        try:
            with tokenize.open(path) as handle:
                tokens = tokenize.generate_tokens(handle.readline)
                for token in tokens:
                    if token.type != tokenize.STRING:
                        continue
                    value = _string_value(token.string)
                    match = MOJIBAKE_RE.search(value)
                    if match:
                        rel = path.relative_to(ROOT)
                        failures.append(
                            f"{rel}:{token.start[0]}: found {match.group(0)!r}",
                        )
        except tokenize.TokenError as exc:
            rel = path.relative_to(ROOT)
            failures.append(f"{rel}: tokenize failed: {exc}")

    if failures:
        print("Likely mojibake in backend Python string literals:")
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
