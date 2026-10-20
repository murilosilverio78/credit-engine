from __future__ import annotations

from typing import Any

from app.services.findings.schemas import Achado, Confianca, Escopo, Estado


def finding(codigo: str, escopo: Escopo, value: Any, *, component: str, path: str,
            state: Estado = Estado.CONFIRMADO, confidence: Confianca = Confianca.ALTA,
            fingerprint: str = "", entity: str | None = None, pendencia: dict | None = None,
            catalog_version: int = 1) -> Achado:
    return Achado(codigo=codigo, catalogo_versao=catalog_version, escopo=escopo, entidade=entity, valor=value, estado=state,
                  confianca=confidence, pendencia=pendencia,
                  evidencia=[{"componente": component, "caminho": path, "fingerprint": fingerprint}])


def unverified(codigo: str, escopo: Escopo, *, component: str, path: str, fingerprint: str) -> Achado:
    return finding(codigo, escopo, None, component=component, path=path, fingerprint=fingerprint,
                   state=Estado.NAO_VERIFICADO, confidence=Confianca.BAIXA,
                   pendencia={"motivo": "fonte_ausente_ou_indisponivel"})
