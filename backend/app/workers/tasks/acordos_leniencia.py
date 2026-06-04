"""
Componente: acordos_leniencia
Consulta acordos de leniência anticorrupção no Portal da Transparência.
Sempre executado — independente das flags do pessoa_juridica.

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

    cnpj_limpo = cnpj.replace(".", "").replace("/", "").replace("-", "")
    headers = {"chave-api-dados": api_token}
    acordos = []

    started = time.monotonic()
    pagina = 1
    while True:
        elapsed = time.monotonic() - started
        if elapsed > MAX_SECONDS:
            raise TimeoutError(f"acordos_leniencia excedeu timeout de {MAX_SECONDS}s na pagina {pagina}")
        if pagina > MAX_PAGES:
            raise TimeoutError(f"acordos_leniencia excedeu limite de {MAX_PAGES} paginas")

        with httpx.Client(timeout=15, verify=False) as client:
            resp = client.get(
                f"{BASE_URL}/acordos-leniencia",
                headers=headers,
                params={"cnpjSancionado": cnpj_limpo, "pagina": pagina},
            )
            resp.raise_for_status()
            data = [] if not resp.content or not resp.text.strip() else resp.json()

        if not data:
            break
        acordos.extend(data)
        pagina += 1

    logger.info(
        "_pagination",
        component="acordos_leniencia",
        paginas=pagina - 1,
        registros=len(acordos),
        elapsed_s=round(time.monotonic() - started, 1),
    )

    return {
        "possui_acordo": len(acordos) > 0,
        "total_acordos": len(acordos),
        "acordos": [
            {
                "situacao": a.get("situacao"),
                "data_inicio": a.get("dataInicioAcordo"),
                "data_fim": a.get("dataFimAcordo"),
                "orgao": a.get("orgaoResponsavel"),
                "objeto": a.get("objeto"),
            }
            for a in acordos
        ],
        "_pagination": {
            "paginas_lidas": pagina,
            "registros": len(acordos),
        },
    }


_task = BaseComponentTask()


def run_acordos_leniencia(operation_id: str):
    return _task.execute(operation_id, component="acordos_leniencia", handler=_fetch)
