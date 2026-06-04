"""
Componente: cnep
Cadastro Nacional de Empresas Punidas — Portal da Transparência.
Executado apenas se pessoa_juridica.sancionado_cnep = True.

Tipo: automatizado | Fila: fast | Cache: 24h
"""
import httpx
import time
from app.workers.base import BaseComponentTask
import structlog

logger = structlog.get_logger()

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
    while True:
        elapsed = time.monotonic() - started
        if elapsed > MAX_SECONDS:
            raise TimeoutError(f"cnep excedeu timeout de {MAX_SECONDS}s na pagina {pagina}")
        if pagina > MAX_PAGES:
            raise TimeoutError(f"cnep excedeu limite de {MAX_PAGES} paginas")

        with httpx.Client(timeout=20, verify=False) as client:
            resp = client.get(
                f"{BASE_URL}/cnep",
                headers=headers,
                params={"codigoSancionado": cnpj, "pagina": pagina},
            )
            resp.raise_for_status()
            data = [] if not resp.content or not resp.text.strip() else resp.json()

        if not data:
            break
        registros.extend(data)
        pagina += 1

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


def run_cnep(operation_id: str):
    return _task.execute(operation_id, component="cnep", handler=_fetch)
