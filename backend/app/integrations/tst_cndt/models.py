"""Public, serialisable result types for the isolated CNDT collector."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class FailureCode(str, Enum):
    CAPTCHA_NAO_RESOLVIDO = "CAPTCHA_NAO_RESOLVIDO"
    PORTAL_INDISPONIVEL = "PORTAL_INDISPONIVEL"
    BLOQUEIO_ANTIBOT = "BLOQUEIO_ANTIBOT"
    FORMULARIO_ALTERADO = "FORMULARIO_ALTERADO"
    PDF_NAO_GERADO = "PDF_NAO_GERADO"
    CNPJ_INVALIDO = "CNPJ_INVALIDO"


@dataclass(frozen=True)
class CndtSuccess:
    """The subset that can later become ``cndt_tst.parsed_result`` unchanged."""

    resultado: str
    data_emissao: str | None
    data_validade: str | None
    numero_certidao: str | None
    cnpj_certidao: str
    orgao_emissor: str
    valida: bool
    fonte: str
    filename: str
    storage_key: str
    status: str = "obtida"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CndtFailure:
    """Failure responses deliberately omit certificate outcome and PDF data."""

    erro: FailureCode
    fonte: str = "tst"
    status: str = "falha"

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "erro": self.erro.value, "fonte": self.fonte}
