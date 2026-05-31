"""
Componente: pessoa_juridica
Entry point do Portal da Transparência.
Retorna flags que controlam quais componentes downstream serão executados.

Tipo: automatizado | Fila: fast | Cache: 12h
"""
import httpx
from app.workers.base import BaseComponentTask
import structlog

logger = structlog.get_logger()

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"


def _fetch(cnpj: str, token: str = None) -> dict:
    from app.core.config import settings
    api_token = token or settings.PORTAL_TRANSPARENCIA_TOKEN

    cnpj_limpo = cnpj.replace(".", "").replace("/", "").replace("-", "")
    headers = {"chave-api-dados": api_token}
    sem_registro = False

    with httpx.Client(timeout=15, verify=False) as client:
        resp = client.get(
            f"{BASE_URL}/pessoa-juridica",
            headers=headers,
            params={"cnpj": cnpj_limpo},
        )
        resp.raise_for_status()
        if not resp.content or not resp.text.strip():
            d = {}
            sem_registro = True
        else:
            d = resp.json()

    flags = {
        "sancionado_ceis":            d.get("sancionadoCEIS", False),
        "sancionado_cnep":            d.get("sancionadoCNEP", False),
        "sancionado_cepim":           d.get("sancionadoCEPIM", False),
        "sancionado_ceaf":            d.get("sancionadoCEAF", False),
        "possui_contratacao":         d.get("possuiContratacao", False),
        "favorecido_despesas":        d.get("favorecidoDespesas", False),
        "favorecido_transferencias":  d.get("favorecidoTransferencias", False),
        "convenios":                  d.get("convenios", False),
        "participa_licitacao":        d.get("participanteLicitacao", False),
        "emitiu_nfe":                 d.get("emitiuNFe", False),
        "beneficiado_renuncia_fiscal":d.get("beneficiadoRenunciaFiscal", False),
    }

    result = {
        "cnpj": cnpj_limpo,
        "razao_social": d.get("razaoSocial"),
        "nome_fantasia": d.get("nomeFantasia"),
        **flags,
        "possui_sancao": any([
            flags["sancionado_ceis"],
            flags["sancionado_cnep"],
            flags["sancionado_cepim"],
            flags["sancionado_ceaf"],
        ]),
    }
    if sem_registro:
        result["erro"] = "sem_registro"
    return result


_task = BaseComponentTask()


def run_pessoa_juridica(operation_id: str):
    return _task.execute(operation_id, component="pessoa_juridica", handler=_fetch)
