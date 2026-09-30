"""Contracts shared by finding adapters and the append-only RPC."""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Escopo(StrEnum):
    CEDENTE = "CEDENTE"
    SACADO = "SACADO"
    CONTRATO = "CONTRATO"
    OPERACAO = "OPERACAO"


class Classe(StrEnum):
    VETO = "VETO"
    CONDICAO = "CONDICAO"
    AJUSTE = "AJUSTE"
    INFORMATIVO = "INFORMATIVO"


class Estado(StrEnum):
    CONFIRMADO = "CONFIRMADO"
    NEGATIVO_CONFIRMADO = "NEGATIVO_CONFIRMADO"
    AMBIGUO = "AMBIGUO"
    NAO_VERIFICADO = "NAO_VERIFICADO"
    CONFLITO = "CONFLITO"


class Confianca(StrEnum):
    ALTA = "ALTA"
    MEDIA = "MEDIA"
    BAIXA = "BAIXA"


class Achado(BaseModel):
    codigo: str
    catalogo_versao: int = 1
    escopo: Escopo
    entidade: str | None = None
    valor: Any = None
    estado: Estado
    confianca: Confianca
    evidencia: list[dict[str, Any]] = Field(default_factory=list)
    valido_ate: str | None = None
    pendencia: dict[str, Any] | None = None


class ExecucaoEnvelope(BaseModel):
    operation_id: str
    ambiente: str
    especialista: str
    status: str
    entrada_hash: str
    versao_schema: str = "1"
    versao_emissor: str = "1"
    modelo: str | None = None
    custo_usd: float | None = None
    duracao_ms: int | None = None
    erro: str | None = None
