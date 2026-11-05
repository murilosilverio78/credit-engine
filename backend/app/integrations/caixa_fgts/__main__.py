"""CLI entry point: ``python -m app.integrations.caixa_fgts``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .client import CrfClient, CrfSettings
from .models import BotBlockedError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consulta a CRF do FGTS no portal da Caixa.")
    parser.add_argument("--cnpj", required=True)
    parser.add_argument("--uf", required=True, help="UF com duas letras, por exemplo SP.")
    parser.add_argument("--out", required=True, type=Path, help="Diretório local do PDF, cache e diagnósticos.")
    parser.add_argument("--debug", action="store_true", help="Salva snapshots sanitizados no diretório de saída.")
    arguments = parser.parse_args(argv)
    client = CrfClient(CrfSettings(output_dir=arguments.out, debug=arguments.debug))
    try:
        result = client.issue(arguments.cnpj, arguments.uf)
    except BotBlockedError as error:
        result = {"status": "falha", "erro": "BLOQUEIO_ANTIBOT", "fonte": "caixa_fgts", "diagnostico": error.diagnostic}
    if arguments.debug:
        print(json.dumps(client.diagnostics, ensure_ascii=False), file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"obtida", "irregular"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
