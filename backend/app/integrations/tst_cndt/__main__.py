"""CLI entry point: ``python -m app.integrations.tst_cndt``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .client import CndtClient, CndtSettings


def main() -> int:
    parser = argparse.ArgumentParser(description="Emite uma CNDT no portal público do TST.")
    parser.add_argument("--cnpj", required=True, help="CNPJ, somente usado no processo local.")
    parser.add_argument("--saida", required=True, type=Path, help="Diretório local do PDF e cache.")
    arguments = parser.parse_args()
    result = CndtClient(CndtSettings(output_dir=arguments.saida)).issue(arguments.cnpj)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "obtida" else 2


if __name__ == "__main__":
    raise SystemExit(main())
