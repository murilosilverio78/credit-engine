"""
Componente: contratos
Consulta contratos administrativos do fornecedor no Portal da Transparência.
Executado apenas se pessoa_juridica.possui_contratacao = True.

Tipo: automatizado | Fila: fast | Cache: 12h
"""
import httpx
import time
from datetime import date
from app.workers.base import BaseComponentTask
import structlog

logger = structlog.get_logger()

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"
MAX_PAGES = 200
MAX_SECONDS = 180


def _is_ativo(c: dict) -> bool:
    fim = c.get("dataFimVigencia") or ""
    try:
        return date.fromisoformat(fim) >= date.today()
    except Exception:
        return False


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

    started = time.monotonic()
    pagina = 1
    while True:
        elapsed = time.monotonic() - started
        if elapsed > MAX_SECONDS:
            raise TimeoutError(f"contratos excedeu timeout de {MAX_SECONDS}s na pagina {pagina}")
        if pagina > MAX_PAGES:
            raise TimeoutError(f"contratos excedeu limite de {MAX_PAGES} paginas")

        with httpx.Client(timeout=20, verify=False) as client:
            resp = client.get(
                f"{BASE_URL}/contratos/cpf-cnpj",
                headers=headers,
                params={"cpfCnpj": cnpj, "pagina": pagina, "tamanhoPagina": 50},
            )
            resp.raise_for_status()
            data = [] if not resp.content or not resp.text.strip() else resp.json()

        if not data:
            break
        contratos.extend(data)
        if len(data) < 50:
            break
        pagina += 1

    parsed    = [_parse_contrato(c) for c in contratos]
    ativos    = [c for c in parsed if c["ativo"]]
    encerrados = [c for c in parsed if not c["ativo"]]

    return {
        "total_contratos": len(parsed),
        "contratos_ativos": len(ativos),
        "contratos_encerrados": len(encerrados),
        "valor_total_ativo": sum(float(c["valor_inicial"] or 0) for c in ativos),
        "valor_total_historico": sum(float(c["valor_inicial"] or 0) for c in parsed),
        "orgaos_contratantes": list({c["orgao"] for c in parsed if c["orgao"]})[:10],
        "contratos_detalhe": parsed,
    }


_task = BaseComponentTask()


def run_contratos(operation_id: str):
    return _task.execute(operation_id, component="contratos", handler=_fetch)
