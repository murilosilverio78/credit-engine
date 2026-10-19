"""Tipos sem dependencias de banco para a politica em modo sombra."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


RATING_REQUIRED_SPECIALISTS = frozenset(
    {"cadastro_regularidade", "sacado_orgao", "porte"}
)


@dataclass(frozen=True)
class FindingValue:
    codigo: str
    valor: Any
    estado: str
    confianca: str
    run_id: str | None = None
    evidencia: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EntradaPolitica:
    operation: dict[str, Any]
    findings: dict[str, FindingValue]
    runs_usados: dict[str, dict[str, str]] = field(default_factory=dict)
    indisponiveis: set[str] = field(default_factory=set)


@dataclass
class ResultadoPolitica:
    score: float | None
    rating: str | None
    rating_potencial: str | None
    merit: float | None
    merit_potencial: float | None
    fator_regularidade: float | None
    fator_potencial: float | None
    penalizacao_balanco: float | None
    limite_aprovado_rs: float | None
    limite_flags: list[str]
    bloqueios: list[str]
    ajuste_pd: dict[str, Any] | None
    dimensoes: dict[str, float]
    efeitos_novos: list[dict[str, Any]]
    trilha: dict[str, list[dict[str, str | None]]]
    condicoes_precedentes: list[dict[str, Any]] = field(default_factory=list)
    alcada_minima: int = 1
    alcada_motivos: list[dict[str, Any]] = field(default_factory=list)
    limite_multiplicadores: list[dict[str, Any]] = field(default_factory=list)
    taxa_ajuste_pp: float = 0.0
    informativos: list[dict[str, Any]] = field(default_factory=list)
    status_decisao: str = "DECISAO_CALCULADA"
    faltantes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "rating": self.rating,
            "rating_potencial": self.rating_potencial,
            "merit": self.merit,
            "merit_potencial": self.merit_potencial,
            "fator_regularidade": self.fator_regularidade,
            "fator_potencial": self.fator_potencial,
            "penalizacao_balanco": self.penalizacao_balanco,
            "limite_aprovado_rs": self.limite_aprovado_rs,
            "limite_flags": self.limite_flags,
            "bloqueios": self.bloqueios,
            "ajuste_pd": self.ajuste_pd,
            "dimensoes": self.dimensoes,
            "efeitos_novos": self.efeitos_novos,
            "trilha": self.trilha,
            "condicoes_precedentes": self.condicoes_precedentes,
            "alcada_minima": self.alcada_minima,
            "alcada_motivos": self.alcada_motivos,
            "limite_multiplicadores": self.limite_multiplicadores,
            "taxa_ajuste_pp": self.taxa_ajuste_pp,
            "informativos": self.informativos,
            "status_decisao": self.status_decisao,
            "faltantes": self.faltantes,
        }
