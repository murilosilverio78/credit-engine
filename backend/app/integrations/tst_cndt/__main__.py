"""CLI entry point: ``python -m app.integrations.tst_cndt``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .client import CndtClient, CndtSettings


def main() -> int:
    parser = argparse.ArgumentParser(description="Emite uma CNDT no portal público do TST.")
    parser.add_argument("--cnpj", required=True, help="CNPJ, somente usado no processo local.")
    parser.add_argument("--saida", required=True, type=Path, help="Diretório local do PDF e cache.")
    parser.add_argument("--debug", action="store_true", help="Diagnóstico seguro em stderr; salva a última resposta do portal.")
    arguments = parser.parse_args()
    client = CndtClient(CndtSettings(output_dir=arguments.saida, debug=arguments.debug))
    result = client.issue(arguments.cnpj)
    if arguments.debug:
        print(json.dumps(client.diagnostics, ensure_ascii=False), file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "obtida" else 2


if __name__ == "__main__":
    raise SystemExit(main())
