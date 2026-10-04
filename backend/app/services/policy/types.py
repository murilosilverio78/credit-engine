"""Tipos sem dependencias de banco para a politica em modo sombra."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


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
    score: float
    rating: str
    rating_potencial: str
    merit: float
    merit_potencial: float | None
    fator_regularidade: float
    fator_potencial: float
    penalizacao_balanco: float | None
    limite_aprovado_rs: float
    limite_flags: list[str]
    bloqueios: list[str]
    ajuste_pd: dict[str, Any] | None
    dimensoes: dict[str, float]
    efeitos_novos: list[dict[str, Any]]
    trilha: dict[str, list[dict[str, str | None]]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score, "rating": self.rating,
            "rating_potencial": self.rating_potencial, "merit": self.merit,
            "merit_potencial": self.merit_potencial,
            "fator_regularidade": self.fator_regularidade,
            "fator_potencial": self.fator_potencial,
            "penalizacao_balanco": self.penalizacao_balanco,
            "limite_aprovado_rs": self.limite_aprovado_rs,
            "limite_flags": self.limite_flags,
            "bloqueios": self.bloqueios, "ajuste_pd": self.ajuste_pd,
            "dimensoes": self.dimensoes, "efeitos_novos": self.efeitos_novos,
            "trilha": self.trilha,
        }
