"""
Componente: recursos_recebidos
Consulta recursos federais recebidos via despesas no Portal da Transparência.
Executado apenas se pessoa_juridica.favorecido_despesas = True.

Tipo: automatizado | Fila: fast | Cache: 12h
"""
import httpx
from datetime import date
from app.workers.celery_app import celery_app
from app.workers.base import BaseComponentTask
import structlog

logger = structlog.get_logger()

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"


def _mes_ano(d: date) -> str:
    return d.strftime("%m/%Y")


def _fetch(cnpj: str, token: str = None) -> dict:
    from app.core.config import settings
    api_token = token or settings.PORTAL_TRANSPARENCIA_TOKEN

    headers = {"chave-api-dados": api_token}

    hoje = date.today()
    mes_fim    = _mes_ano(hoje)
    mes_inicio = _mes_ano(hoje.replace(year=hoje.year - 1))

    recursos = []

    pagina = 1
    while True:
        # URL montada como string para evitar encoding do "/" pelo httpx
        url = (
            f"{BASE_URL}/despesas/recursos-recebidos"
            f"?codigoFavorecido={cnpj}"
            f"&mesAnoInicio={mes_inicio}"
            f"&mesAnoFim={mes_fim}"
            f"&pagina={pagina}"
        )
        with httpx.Client(timeout=20, verify=False) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if not data:
            break
        recursos.extend(data)
        if len(data) < 10:
            break
        pagina += 1

    valor_total = sum(float(r.get("valor") or 0) for r in recursos)
    orgaos = list({r.get("nomeOrgao", "") for r in recursos if r.get("nomeOrgao")})

    por_ano = {}
    for r in recursos:
        ano = str(r.get("anoMes") or 0)[:4]
        if ano:
            por_ano[ano] = por_ano.get(ano, 0) + float(r.get("valor") or 0)

    return {
        "total_registros": len(recursos),
        "valor_total_recebido": valor_total,
        "periodo_inicio": mes_inicio,
        "periodo_fim": mes_fim,
        "orgaos_pagadores": orgaos[:10],
        "valor_por_ano": por_ano,
        "recursos_detalhe": [
            {
                "mes": r.get("anoMes"),
                "valor": r.get("valor"),
                "orgao": r.get("nomeOrgao"),
                "acao": r.get("nomeAcao"),
            }
            for r in recursos[:50]
        ],
    }


@celery_app.task(
    bind=True,
    base=BaseComponentTask,
    queue="fast",
    name="recursos_recebidos.run",
    max_retries=3,
    default_retry_delay=10,
)
def run_recursos_recebidos(self, operation_id: str):
    return self.execute(operation_id, component="recursos_recebidos", handler=_fetch)
