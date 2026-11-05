"""Serializable results and explicit stop conditions for the CRF PoC."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class FailureCode(str, Enum):
    CNPJ_INVALIDO = "CNPJ_INVALIDO"
    UF_INVALIDA = "UF_INVALIDA"
    CAPTCHA_NAO_RESOLVIDO = "CAPTCHA_NAO_RESOLVIDO"
    FORMULARIO_ALTERADO = "FORMULARIO_ALTERADO"
    PORTAL_INDISPONIVEL = "PORTAL_INDISPONIVEL"
    PDF_NAO_GERADO = "PDF_NAO_GERADO"


class BotBlockedError(RuntimeError):
    """The portal asked for a challenge; the collector must stop, never evade."""

    def __init__(self, *, status: int | None, title: str, html_excerpt: str) -> None:
        self.diagnostic = {
            "status": status,
            "title": title[:160],
            "html_excerpt": html_excerpt[:500],
        }
        super().__init__(f"bloqueio anti-bot: status={status!s}, titulo={title[:80]!r}")


@dataclass(frozen=True)
class CrfSuccess:
    situacao: str
    numero_certificado: str | None
    data_emissao: str | None
    data_inicio_validade: str | None
    data_fim_validade: str | None
    cnpj_certidao: str
    razao_social: str | None
    filename: str
    storage_key: str
    fonte: str = "caixa_fgts"
    status: str = "obtida"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CrfIrregular:
    situacao: str = "irregular"
    fonte: str = "caixa_fgts"
    status: str = "irregular"
    filename: None = None
    storage_key: None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CrfFailure:
    erro: FailureCode
    fonte: str = "caixa_fgts"
    status: str = "falha"

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "erro": self.erro.value, "fonte": self.fonte}
