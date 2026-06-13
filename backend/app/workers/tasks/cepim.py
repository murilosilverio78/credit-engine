"""
Componente: cepim
Cadastro de Entidades Privadas Sem Fins Lucrativos Impedidas — Portal da Transparência.
Executado apenas se pessoa_juridica.sancionado_cepim = True.

Tipo: automatizado | Fila: fast | Cache: 24h
"""
import os

import httpx
import time
from app.workers.base import BaseComponentTask
from app.workers.http_utils import fetch_json_with_retry
import structlog

logger = structlog.get_logger()

SSL_VERIFY = os.getenv("SSL_VERIFY", "true").lower() != "false"
BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"
MAX_PAGES = 200
MAX_SECONDS = 180


def _fetch(cnpj: str, token: str = None) -> dict:
    from app.core.config import settings
    api_token = token or settings.PORTAL_TRANSPARENCIA_TOKEN

    headers = {"chave-api-dados": api_token}
    registros = []

    started = time.monotonic()
    pagina = 1
    with httpx.Client(timeout=20, verify=SSL_VERIFY) as client:
        while True:
            elapsed = time.monotonic() - started
            if elapsed > MAX_SECONDS:
                raise TimeoutError(f"cepim excedeu timeout de {MAX_SECONDS}s na pagina {pagina}")
            if pagina > MAX_PAGES:
                raise TimeoutError(f"cepim excedeu limite de {MAX_PAGES} paginas")

            data = fetch_json_with_retry(
                client,
                f"{BASE_URL}/cepim",
                headers=headers,
                params={"cnpjSancionado": cnpj, "pagina": pagina},
            )

            if not data:
                break
            registros.extend(data)
            pagina += 1

    logger.info(
        "_pagination",
        component="cepim",
        paginas=pagina - 1,
        registros=len(registros),
        elapsed_s=round(time.monotonic() - started, 1),
    )

    return {
        "possui_sancao": len(registros) > 0,
        "total_registros": len(registros),
        "registros": registros,
        "_pagination": {
            "paginas_lidas": pagina,
            "registros": len(registros),
        },
    }


_task = BaseComponentTask()


def run_cepim(operation_id: str):
    return _task.execute(operation_id, component="cepim", handler=_fetch)
