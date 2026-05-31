"""
Componente: cnep
Cadastro Nacional de Empresas Punidas — Portal da Transparência.
Executado apenas se pessoa_juridica.sancionado_cnep = True.

Tipo: automatizado | Fila: fast | Cache: 24h
"""
import httpx
from app.workers.base import BaseComponentTask
import structlog

logger = structlog.get_logger()

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"


def _fetch(cnpj: str, token: str = None) -> dict:
    from app.core.config import settings
    api_token = token or settings.PORTAL_TRANSPARENCIA_TOKEN

    headers = {"chave-api-dados": api_token}
    registros = []

    pagina = 1
    while True:
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
        if len(data) < 10:
            break
        pagina += 1

    return {
        "possui_sancao": len(registros) > 0,
        "total_registros": len(registros),
        "registros": registros,
    }


_task = BaseComponentTask()


def run_cnep(operation_id: str):
    return _task.execute(operation_id, component="cnep", handler=_fetch)
