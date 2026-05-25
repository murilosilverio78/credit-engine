"""
Componente: Portal da Transparência
Consulta contratos administrativos do fornecedor (cedente).
Tipo: automatizado | Fila: fast | Cache: 12h
"""
import httpx
from app.workers.celery_app import celery_app
from app.workers.base import BaseComponentTask
import structlog

logger = structlog.get_logger()

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"


def _is_ativo(contrato: dict) -> bool:
    situacao = (contrato.get("situacaoContrato") or "").lower()
    fim = contrato.get("dataFimVigencia") or ""
    from datetime import date
    try:
        data_fim = date.fromisoformat(fim)
        return data_fim >= date.today()
    except Exception:
        return "ativo" in situacao or "vigente" in situacao or situacao == "não se aplica"


def _get_orgao(c: dict) -> str:
    try:
        return c["unidadeGestora"]["orgaoMaximo"]["nome"]
    except (KeyError, TypeError):
        return ""


def _parse_contrato(c: dict) -> dict:
    return {
        "numero": c.get("numero"),
        "objeto": (c.get("objeto") or "").replace("Objeto: ", "").strip(),
        "situacao": c.get("situacaoContrato"),
        "valor_inicial": c.get("valorInicialCompra"),
        "valor_final": c.get("valorFinalCompra"),
        "data_assinatura": c.get("dataAssinatura"),
        "data_inicio": c.get("dataInicioVigencia"),
        "data_fim": c.get("dataFimVigencia"),
        "orgao": _get_orgao(c),
        "unidade": (c.get("unidadeGestora") or {}).get("nome"),
        "ativo": _is_ativo(c),
    }


def _fetch(cnpj: str, token: str = None) -> dict:
    from app.core.config import settings
    api_token = token or settings.PORTAL_TRANSPARENCIA_TOKEN

    headers = {"chave-api-dados": api_token}
    contratos = []
    pagina = 1

    with httpx.Client(timeout=20) as client:
        while True:
            resp = client.get(
                f"{BASE_URL}/contratos/cpf-cnpj",
                headers=headers,
                params={"cpfCnpj": cnpj, "pagina": pagina, "tamanhoPagina": 50},
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            contratos.extend(data)
            if len(data) < 50:
                break
            pagina += 1

    parsed = [_parse_contrato(c) for c in contratos]
    ativos = [c for c in parsed if c["ativo"]]
    encerrados = [c for c in parsed if not c["ativo"]]

    valor_ativo = sum(float(c["valor_inicial"] or 0) for c in ativos)
    valor_historico = sum(float(c["valor_inicial"] or 0) for c in parsed)

    orgaos = list({c["orgao"] for c in parsed if c["orgao"]})

    return {
        "total_contratos": len(parsed),
        "contratos_ativos": len(ativos),
        "contratos_encerrados": len(encerrados),
        "valor_total_ativo": valor_ativo,
        "valor_total_historico": valor_historico,
        "orgaos_contratantes": orgaos[:10],
        "contratos_detalhe": parsed[:20],
    }


@celery_app.task(
    bind=True,
    base=BaseComponentTask,
    queue="fast",
    name="portal_transparencia.run",
    max_retries=3,
    default_retry_delay=10,
)
def run_portal_transparencia(self, operation_id: str):
    return self.execute(operation_id, component="portal_transparencia", handler=_fetch)
