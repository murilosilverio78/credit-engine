"""Differential parity between policy v0 and the deterministic score engine.

The seed is fixed deliberately: a failure prints both the seed and the full
case payload, so it is reproducible without weakening any comparison.
"""
from __future__ import annotations

import json
import random
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.services.findings.adapters.cadastro_regularidade import emit_cadastro_regularidade
from app.services.findings.adapters.porte import emit_porte
from app.services.findings.adapters.reputacional import emit_reputacional
from app.services.findings.adapters.sacado_orgao import emit_sacado_orgao
from app.services.policy.engine import avaliar
from app.services.policy.types import EntradaPolitica, FindingValue
from app.workers.tasks import score_engine


SEED = 20261003
SYNTHETIC_CASES = 300
SQL = Path(__file__).parents[2] / "infra/supabase/migrations/045_politica_v0.sql"
VETO_RULES = [
    {"classe": "VETO", "codigo": "cadastro_inativo"},
    {"classe": "VETO", "codigo": "sancao_ativa"},
    {"classe": "VETO", "codigo": "acordo_leniencia_ativo"},
]


def _seed_params() -> dict[str, Any]:
    text = SQL.read_text(encoding="utf-8")
    return {
        key: json.loads(value)
        for key, value in re.findall(r"\('([^']+)', '(.+?)'::JSONB\)", text)
    }


PARAMS = _seed_params()
MATRIX = {rating: {"pd_mult": multiplier} for rating, multiplier in PARAMS["pd_mult_por_rating"].items()}


def _certificate(kind: str, reference: date) -> dict[str, Any] | None:
    if kind == "ausente":
        return None
    if kind == "vencida":
        return {"resultado": "negativa", "valida": True, "data_validade": (reference - timedelta(days=1)).isoformat()}
    if kind == "nao_validada":
        return {"resultado": "negativa", "valida": False}
    if kind == "positiva":
        return {"resultado": "positiva", "valida": True, "data_validade": (reference + timedelta(days=30)).isoformat()}
    if kind == "positiva_com_efeitos":
        return {"resultado": "positiva_com_efeitos", "valida": True, "data_validade": (reference + timedelta(days=30)).isoformat()}
    return {"resultado": "negativa", "valida": True, "data_validade": (reference + timedelta(days=30)).isoformat()}


def _snapshot(case: dict[str, Any], reference: date) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    age = float(case["age"])
    opened = reference - timedelta(days=round(age * 365.25))
    contract_count = int(case["active_contracts"])
    duration = float(case["duration"])
    contracts = [
        {
            "ativo": True,
            "orgao": f"ORGAO-{index % int(case['org_count'])}",
            "data_inicio": (reference - timedelta(days=round(duration * 365.25))).isoformat(),
            "data_fim": reference.isoformat(),
        }
        for index in range(contract_count)
    ]
    years = case["series_years"]
    documents_source = case["balance_source"]
    snapshots: dict[str, Any] = {
        "__statuses__": {component: "completed" for component in ("brasil_api", "pessoa_juridica", "ceis", "cnep", "cepim", "ceaf", "acordos_leniencia")},
        "brasil_api": {
            "situacao_cadastral": case["situacao"],
            "data_abertura": opened.isoformat(),
            "capital_social": case["capital"],
            "porte": case["porte_cadastral"],
            "natureza_juridica": case["natureza"],
            "qsa": [{"data_entrada": (reference - timedelta(days=round(float(case["qsa_years"]) * 365.25))).isoformat(), "qualificacao": "Administrador"}],
        },
        "pessoa_juridica": {},
        "ceis": {"total_registros": 0}, "cnep": {"total_registros": 0}, "cepim": {"total_registros": 0}, "ceaf": {"total_registros": 0},
        "acordos_leniencia": {"total_acordos": 0},
        "contratos": {
            "contratos_ativos": contract_count,
            "total_contratos": case["total_contracts"],
            "orgaos_contratantes": [f"ORGAO-{index}" for index in range(int(case["org_count"]))],
            "contratos_detalhe": contracts,
            "valor_total_ativo": case["valor_total_ativo"],
            "contratos_comprasnet_incluidos": case["comprasnet_incluidos"],
        },
        "recursos_recebidos": {
            "concentracao": {"hhi": case["hhi"]} if case["hhi"] is not None else {},
            "meses_com_recebimento": case["months"],
            "volatilidade": {"cv": case["cv"], "anos_completos": case["anos_informados"]},
            "serie_anual": {str(year): 1000 for year in years},
        },
        "web_research": {
            "nivel": case["reputacao"],
            "fatores_reputacao": ["sinal positivo"] if case["reputacao"] == "Excepcional" else [],
            "flags_reputacao": case["reputacao_flags"],
        },
        "score_engine": {"dimensoes": {"porte_operacionalidade": {"nivel": case["porte_llm"], "flags": case["porte_flags"]}}},
    }
    for component, kind in zip(("cnd_federal", "cndt_tst", "fgts"), case["certidoes"]):
        certificate = _certificate(kind, reference)
        if certificate is not None:
            snapshots[component] = certificate
        else:
            snapshots["__statuses__"][component] = "pending"
    document = {"tipo": "DRE"}
    if documents_source == "cotacao":
        snapshots["catalogo_broadfactor"] = {"documentos_broadfactor": [document]}
    elif documents_source == "extracao":
        snapshots["contrato_extracao"] = {"resultado": {"tipo_documento": "DRE"}}
    elif documents_source == "documents":
        snapshots["documentos_operacao"] = {"documentos": [{"document_type": "DRE"}]}
    elif documents_source == "ambos":
        snapshots["catalogo_broadfactor"] = {"documentos_broadfactor": [document]}
        snapshots["contrato_extracao"] = {"resultado": {"tipo_documento": "DRE"}}

    operation = {
        "valor_enquadrado": case["valor_enquadrado"], "valor_solicitado": case["valor_solicitado"],
        "margem_disponivel": case["margem_disponivel"], "contrato_saldo": case["contrato_saldo"],
        "saldo_vincendo": case["saldo_vincendo"], "pct_max_contrato": case["pct_max_contrato"],
    }
    porte_dimension = score_engine._dimension(
        score_engine.NIVEL_NOTA[case["porte_llm"]], score_engine.PESOS_MERITO["porte_operacionalidade"], [], "teste diferencial", case["porte_flags"], nivel=case["porte_llm"],
    )
    return snapshots, operation, porte_dimension


def _findings(snapshots: dict[str, Any], operation: dict[str, Any]) -> dict[str, FindingValue]:
    candidates = [
        *emit_cadastro_regularidade(snapshots, fingerprint="diferencial", operation=operation),
        *emit_sacado_orgao(snapshots, fingerprint="diferencial", operation=operation),
        *emit_porte(snapshots, fingerprint="diferencial", operation=operation),
        *emit_reputacional(snapshots, fingerprint="diferencial", operation=operation),
    ]
    return {
        candidate.codigo: FindingValue(
            candidate.codigo, candidate.valor, candidate.estado.value, candidate.confianca.value,
            "diferencial", list(candidate.evidencia),
        )
        for candidate in candidates
    }


def _compare(case: dict[str, Any], reference: date) -> list[str]:
    snapshots, operation, porte_dimension = _snapshot(case, reference)
    official = score_engine.consolidar_score("12345678000199", snapshots, operation, porte_dimension=porte_dimension)
    policy = avaliar(EntradaPolitica(operation, _findings(snapshots, operation)), PARAMS, VETO_RULES, reference)
    actual = policy.as_dict()
    expected = {
        "score": official.get("score"), "rating": official.get("rating"), "rating_potencial": official.get("rating_potencial"),
        "merit": official.get("merit"), "merit_potencial": official.get("merit_potencial", "<ausente no retorno oficial>"),
        "fator_regularidade": official.get("fator_regularidade"), "fator_potencial": official.get("regularidade", {}).get("fator_potencial"),
        "penalizacao_balanco": official.get("penalizacao_balanco", 0.0), "limite_aprovado_rs": official.get("limite_aprovado_rs"),
        "limite_flags": [flag for flag in official.get("flags", []) if flag.startswith("limite_")],
        "bloqueios": official.get("bloqueios"), "ajuste_pd": official.get("ajuste_pd"),
        "dimensoes": {name: dimension["score"] for name, dimension in official.get("dimensoes", {}).items()},
    }
    return [f"{field}: politica={actual.get(field)!r}; oficial={value!r}" for field, value in expected.items() if actual.get(field) != value]


def _base_case() -> dict[str, Any]:
    return {
        "age": 4.0, "capital": 300000, "porte_cadastral": "EPP", "natureza": "Sociedade empresaria limitada", "qsa_years": 2.0,
        "situacao": "ATIVA", "active_contracts": 3, "total_contracts": 3, "org_count": 2, "duration": 4.0, "valor_total_ativo": 500000,
        "comprasnet_incluidos": 0, "hhi": 3000.0, "months": 8, "cv": 0.5, "anos_informados": 2, "series_years": [2024, 2025],
        "porte_llm": "Adequado", "porte_flags": [], "reputacao": "Adequado", "reputacao_flags": [],
        "certidoes": ("negativa", "negativa", "negativa"), "balance_source": "nenhuma",
        "valor_enquadrado": 100000, "valor_solicitado": 120000, "margem_disponivel": 0, "contrato_saldo": 0, "saldo_vincendo": 0, "pct_max_contrato": 0.2,
    }


def _synthetic_cases() -> list[pytest.ParameterSet]:
    rng = random.Random(SEED)
    cases = []
    ages = (0.5, 1.0, 1.01, 2.0, 2.01, 5.0, 5.01, 10.0, 10.01, 20.0, 20.01)
    capitals = (9999, 10000, 10001, 49999, 50000, 50001, 199999, 200000, 200001, 499999, 500000, 500001, 2000000, 2000001)
    for index in range(SYNTHETIC_CASES):
        case = _base_case()
        case.update({
            "age": rng.choice(ages), "capital": rng.choice(capitals), "porte_cadastral": rng.choice(("MEI", "MICRO", "EPP", "MEDIO", "GRANDE", "OUTRO")),
            "qsa_years": rng.choice((0.5, 1.0, 1.01, 3.0, 3.01, 5.0)), "active_contracts": rng.choice((0, 1, 2, 4, 5, 9, 10)),
            "total_contracts": rng.choice((0, 2, 3, 5, 6, 12, 13)), "org_count": rng.choice((1, 2, 3, 4, 5)),
            "duration": rng.choice((0.5, 1.0, 2.99, 3.0, 5.0)), "hhi": rng.choice((None, 1000.0, 2499.9, 2500.0, 6000.0, 6000.1)),
            "months": rng.choice((0, 5, 6, 8, 12)), "cv": rng.choice((None, 0.0, 0.7, 0.71, 0.8, 0.81)),
            "anos_informados": rng.choice((None, 0, 1, 2, 3)), "series_years": rng.choice(([], [reference_year := date.today().year - 1], [reference_year - 1, reference_year])),
            "porte_llm": rng.choice(tuple(score_engine.NIVEL_NOTA)), "reputacao": rng.choice(tuple(score_engine.NIVEL_NOTA)),
            "certidoes": tuple(rng.choice(("ausente", "vencida", "nao_validada", "positiva", "positiva_com_efeitos", "negativa")) for _ in range(3)),
            "balance_source": rng.choice(("nenhuma", "cotacao", "extracao", "documents", "ambos")), "comprasnet_incluidos": rng.choice((0, 1)),
        })
        if case["porte_cadastral"] == "OUTRO":
            case["capital"] = min(case["capital"], 300000)
        cases.append(pytest.param(f"sintetico-{index:03d}", case, id=f"sintetico-{index:03d}"))
    return cases


def _fixed_cases() -> list[pytest.ParameterSet]:
    reference_year = date.today().year
    cases: list[tuple[str, dict[str, Any]]] = []
    for source in ("nenhuma", "ambos", "cotacao", "extracao", "documents"):
        case = _base_case(); case["balance_source"] = source; cases.append((f"balanco-{source}", case))
    for certificate in ("ausente", "vencida", "nao_validada", "positiva", "positiva_com_efeitos", "negativa"):
        case = _base_case(); case["certidoes"] = (certificate,) * 3; cases.append((f"certidao-{certificate}", case))
    for hhi, months in ((1000.0, 5), (3000.0, 6), (7000.0, 12), (None, 0)):
        case = _base_case(); case.update(hhi=hhi, months=months); cases.append((f"hhi-{hhi}-{months}", case))
    for cv, informed, series in ((None, 2, [reference_year - 2]), (0.0, None, [reference_year - 1]), (None, None, [reference_year - 1]), (0.81, 2, [reference_year - 2, reference_year - 1])):
        case = _base_case(); case.update(cv=cv, anos_informados=informed, series_years=series); cases.append((f"pd-{cv}-{informed}-{len(series)}", case))
    for operation in (
        {"valor_enquadrado": 0, "margem_disponivel": 0, "saldo_vincendo": 0, "contrato_saldo": 500000, "pct_max_contrato": .2},
        {"valor_enquadrado": 0, "margem_disponivel": 0, "saldo_vincendo": 0, "contrato_saldo": 0, "pct_max_contrato": .2},
        {"valor_enquadrado": 0, "margem_disponivel": 70000, "saldo_vincendo": 1, "contrato_saldo": 1, "pct_max_contrato": .2},
    ):
        case = _base_case(); case.update(operation); cases.append((f"limite-{len(cases)}", case))
    case = _base_case(); case.update(porte_cadastral="OUTRO", natureza="Associacao privada"); cases.append(("porte-nao-empresarial", case))
    case = _base_case(); case.update(porte_llm="Atencao", porte_flags=["parse_falhou"]); cases.append(("porte-parse-falhou", case))
    case = _base_case(); case.update(reputacao="Atencao", reputacao_flags=["reputacao_parse_falhou"]); cases.append(("reputacao-parse-falhou", case))
    case = _base_case(); case.update(
        age=4.67, capital=370000, qsa_years=2.0, hhi=5472.4, months=8,
        porte_llm="Atencao", reputacao="Atencao", balance_source="cotacao",
        valor_enquadrado=114401.4, valor_solicitado=120000,
    ); cases.append(("golden-99e4d490", case))
    case = _base_case(); case.update(comprasnet_incluidos=1, valor_total_ativo=750000); cases.append(("comprasnet-mesclado", case))
    case = _base_case(); case.update(balance_source="nenhuma", porte_llm="Critico"); cases.append(("balanco-teto-porte-baixo", case))
    case = _base_case(); case.update(situacao="BAIXADA"); cases.append(("veto-cadastro", case))
    return [pytest.param(name, case, id=name) for name, case in cases]


@pytest.fixture(autouse=True)
def pricing_seed(monkeypatch):
    import app.services.pricing_params_service as pricing

    monkeypatch.setattr(pricing, "get_pricing_config", lambda: (PARAMS, MATRIX))


@pytest.mark.parametrize(("name", "case"), _synthetic_cases() + _fixed_cases())
def test_policy_matches_official_score_engine(name: str, case: dict[str, Any]):
    reference = date.today()
    differences = _compare(case, reference)
    assert not differences, (
        f"seed={SEED}; case={name}; payload={json.dumps(case, sort_keys=True)}; "
        f"divergencias={' | '.join(differences)}"
    )
