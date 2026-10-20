"""Shadow comparison and append-only persistence for policy v0."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

import structlog

from app.services.policy.engine import avaliar
from app.services.policy.inputs import montar_entrada
from app.services.policy.loader import load_policy

# Bump whenever shadow comparison or classification semantics change.
SHADOW_LOGIC_VERSION = "5"
_FIELDS = (
    "merit",
    "merit_potencial",
    "fator_regularidade",
    "fator_potencial",
    "penalizacao_balanco",
    "score",
    "rating",
    "rating_potencial",
    "limite_aprovado_rs",
    "bloqueios",
    "ajuste_pd",
)
_PARITY_SPECIALISTS = frozenset({"cadastro_regularidade", "sacado_orgao"})
_AJUSTE_PD_FIELDS = (
    "faixa_volatilidade",
    "multiplicador_volatilidade",
    "pd_base",
    "pd_ajustada",
)
_AJUSTE_PD_CURRENT_FIELDS = frozenset({"min_anos_completos", "anos_completos"})
_INPUT_COMPONENTS = frozenset(
    {
        "brasil_api",
        "pessoa_juridica",
        "ceis",
        "cnep",
        "cepim",
        "acordos_leniencia",
        "cnd_federal",
        "cndt_tst",
        "fgts",
        "ceaf",
        "contrato_extracao",
        "contratos",
        "recursos_recebidos",
        "contratos_comprasnet",
        "web_research",
    }
)
_CERTIFICATE_FINDINGS = frozenset(
    {
        "certidao_cnd_federal_pendente",
        "certidao_cndt_pendente",
        "certidao_fgts_pendente",
    }
)
# These are direct consequences of a regularity factor.  Any divergence in a
# merit, balance or block field is independent evidence and must not be
# hidden as a temporal certificate input.
_TEMPORAL_CERTIFICATE_CASCADE_FIELDS = frozenset(
    {
        "fator_regularidade",
        "fator_potencial",
        "score",
        "rating",
        "limite_aprovado_rs",
    }
)

logger = structlog.get_logger()


def _rows(query) -> list[dict[str, Any]]:
    result = query.execute()
    return list((result.data or []) if result else [])


def _reference(value: Any) -> date:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return date.today()


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        try:
            return datetime.strptime(str(value), "%d/%m/%Y").date()
        except (ValueError, TypeError):
            return None


def _today() -> date:
    return date.today()


def _official(database, operation_id: str) -> tuple[dict[str, Any] | None, date]:
    rows = _rows(
        database.table("component_snapshots")
        .select("parsed_result,completed_at,created_at")
        .eq("operation_id", operation_id)
        .eq("component", "score_engine")
        .eq("status", "completed")
        .order("completed_at", desc=True)
        .limit(1)
    )
    if not rows or not isinstance(rows[0].get("parsed_result"), dict):
        return None, date.today()
    official = dict(rows[0]["parsed_result"])
    # Snapshots reais do score persistem esse fator dentro de regularidade;
    # versões anteriores também podem tê-lo no nível superior.
    if "fator_potencial" not in official and isinstance(
        official.get("regularidade"), dict
    ):
        official["fator_potencial"] = official["regularidade"].get("fator_potencial")
    return official, _reference(
        rows[0].get("completed_at") or rows[0].get("created_at")
    )


def _input_components_after_score(database, operation_id: str) -> list[str]:
    """Return completed score inputs newer than the persisted official score."""
    try:
        score_rows = _rows(
            database.table("component_snapshots")
            .select("completed_at,created_at")
            .eq("operation_id", operation_id)
            .eq("component", "score_engine")
            .eq("status", "completed")
        )
        if not score_rows:
            return []
        score_completed_at = max(
            (
                timestamp
                for row in score_rows
                if (
                    timestamp := _timestamp(
                        row.get("completed_at") or row.get("created_at")
                    )
                )
                is not None
            ),
            default=None,
        )
        if score_completed_at is None:
            return []
        rows = _rows(
            database.table("component_snapshots")
            .select("component,completed_at")
            .eq("operation_id", operation_id)
            .eq("status", "completed")
        )
    except Exception:
        return []
    return sorted(
        {
            str(row["component"])
            for row in rows
            if row.get("component") in _INPUT_COMPONENTS
            and (_timestamp(row.get("completed_at")) or score_completed_at)
            > score_completed_at
        }
    )


def _set_pct_max_contrato(
    operation: dict[str, Any], official: dict[str, Any] | None
) -> None:
    """Use the exact historical cap, falling back to the score's config source."""
    if (
        isinstance(official, dict)
        and official.get("limite_sugerido_pct_contrato") is not None
    ):
        operation["pct_max_contrato"] = official["limite_sugerido_pct_contrato"]
        operation["pct_max_contrato_origem"] = "oficial"
        return
    try:
        from app.services.eligibility_params_service import get_eligibility_config

        operation["pct_max_contrato"] = get_eligibility_config()["pct_max_contrato"]
        operation["pct_max_contrato_origem"] = "eligibility"
    except Exception:
        # Keep it absent: _limit emits limite_sem_pct_max_contrato just as the
        # deterministic score does when the cap is unavailable.
        operation.pop("pct_max_contrato", None)
        operation["pct_max_contrato_origem"] = "indisponivel"


def _block_category(value: Any) -> str:
    text = str(value)
    if text.startswith("Situacao cadastral") or text == "cadastro_inativo":
        return "cadastro_inativo"
    if text.startswith("Sancao ativa") or text == "sancao_ativa":
        return "sancao_ativa"
    if text.startswith("Acordo de leniencia") or text == "acordo_leniencia_ativo":
        return "acordo_leniencia_ativo"
    return text


def _effects_explain(
    divergencias: list[dict[str, Any]],
    effects: list[dict[str, Any]],
    limite_multiplicadores: list[dict[str, Any]] | None = None,
) -> bool:
    """Only explicitly declared parity fields can justify a divergence.

    Current glosa and conta-vinculada effects target pricing/LGD, neither of
    which changes the score-parity fields persisted by this shadow runner.
    """
    fields = {str(item["campo"]) for item in divergencias}
    explained = {
        str(field)
        for effect in effects
        for field in (effect.get("campos_paridade") or [])
    }
    for divergence in divergencias:
        if divergence.get("campo") != "limite_aprovado_rs":
            continue
        factors = limite_multiplicadores or []
        if not factors:
            continue
        try:
            base = float(divergence.get("valor_oficial"))
            actual = float(divergence.get("valor_politica"))
            expected = base
            for factor in factors:
                expected *= float(factor.get("fator"))
        except (TypeError, ValueError):
            continue
        if abs(actual - expected) <= max(abs(expected), 1.0) * 0.005:
            explained.add("limite_aprovado_rs")
    return bool(fields) and fields <= explained


def _is_temporal_certificate_input(
    entrada,
    official: dict[str, Any],
    computed: dict[str, Any],
    reference: date,
    divergencias: list[dict[str, Any]],
) -> bool:
    """Whether an expired certificate is the sole source of parity drift.

    Findings preserve each certificate's validity date.  A certificate that
    was still valid at the official decision but is expired when findings are
    re-emitted is an input-time difference, rather than an unexpected policy
    result.  Score/rating/limit changes are accepted only as downstream
    consequences of that lower regularity factor.
    """
    fields = {str(item.get("campo")) for item in divergencias}
    if (
        "fator_regularidade" not in fields
        or not fields <= _TEMPORAL_CERTIFICATE_CASCADE_FIELDS
    ):
        return False
    try:
        if float(computed.get("fator_regularidade")) >= float(
            official.get("fator_regularidade")
        ):
            return False
    except (TypeError, ValueError):
        return False
    return any(
        (validade := _date(getattr(finding, "valor", {}).get("validade")))
        and reference < validade <= _today()
        for code, finding in entrada.findings.items()
        if code in _CERTIFICATE_FINDINGS
        and isinstance(getattr(finding, "valor", None), dict)
    )


def versao_arquivada_anterior(
    versao: int | None, *, database=None
) -> int | None:
    """Return the nearest archived predecessor eligible for a policy comparison."""
    if versao is None or database is None:
        return None
    try:
        rows = _rows(
            database.table("policy_versions")
            .select("versao")
            .eq("status", "ARQUIVADA")
            .lt("versao", versao)
            .order("versao", desc=True)
            .limit(1)
        )
        return int(rows[0]["versao"]) if rows else None
    except Exception as exc:
        logger.warning("policy_shadow.previous_version_failed", versao=versao, error=str(exc))
        return None


def executar_sombra_automatica(operation_id: str, *, database=None) -> None:
    """Run automatic shadow safely; it must never affect the official flow."""
    try:
        if database is None:
            from app.core.database import supabase

            database = supabase
        try:
            policy = load_policy(database=database, status="SOMBRA")
            version = policy["version"].get("versao")
        except Exception as exc:
            # The primary run has always been best-effort and does not need a
            # predecessor lookup.  Keep it available if the optional version
            # query is unavailable (including a transient database failure).
            logger.warning(
                "policy_shadow.automatic_reference_lookup_failed",
                operation_id=operation_id,
                error=str(exc),
            )
            avaliar_sombra(operation_id, database=database, aplicar=True)
            return
        previous = versao_arquivada_anterior(version, database=database)
        if previous is None:
            avaliar_sombra(operation_id, database=database, aplicar=True)
            return
        try:
            comparar_sombra(
                operation_id,
                database=database,
                aplicar=True,
                versao=version,
                comparar_com=previous,
            )
        except Exception as exc:
            logger.warning(
                "policy_shadow.automatic_comparison_failed",
                operation_id=operation_id,
                versao=version,
                comparar_com=previous,
                error=str(exc),
            )
            avaliar_sombra(operation_id, database=database, aplicar=True)
    except Exception as exc:
        logger.warning(
            "policy_shadow.automatic_failed",
            operation_id=operation_id,
            error=str(exc),
        )


def _non_comparable(
    field: str, policy_value: Any, official_value: Any, reason: str
) -> dict[str, Any]:
    return {
        "campo": field,
        "valor_politica": policy_value,
        "valor_oficial": official_value,
        "diferenca": None,
        "classe": "NAO_COMPARAVEL",
        "motivo": reason,
    }


def _ajuste_pd_values(policy_value: Any, official_value: Any) -> tuple[Any, Any] | None:
    """Project the current PD contract, or flag old persisted snapshots."""
    if not isinstance(official_value, dict):
        return policy_value, official_value
    if not _AJUSTE_PD_CURRENT_FIELDS <= set(official_value):
        return None
    policy_pd = policy_value if isinstance(policy_value, dict) else {}
    return (
        {key: policy_pd.get(key) for key in _AJUSTE_PD_FIELDS},
        {key: official_value.get(key) for key in _AJUSTE_PD_FIELDS},
    )


def _missing_parity_specialists(
    runs_usados: dict[str, dict[str, str]], official: dict[str, Any] | None
) -> set[str]:
    """Return only the runs needed to compare the persisted score.

    ``documentos`` feeds a policy effect, but not score parity.  Porte and
    reputacao are only comparable when an official score exists.
    """
    required = _PARITY_SPECIALISTS | (
        {"reputacional", "porte"} if official is not None else set()
    )
    return required - set(runs_usados)


def _hash(
    runs: dict[str, dict[str, str]],
    operation: dict[str, Any],
    policy_id: str,
    policy_content_hash: str,
    ref: date,
) -> str:
    context = {
        key: operation.get(key)
        for key in (
            "valor_enquadrado",
            "valor_solicitado",
            "pct_max_contrato",
            "margem_disponivel",
            "contrato_saldo",
            "saldo_vincendo",
        )
    }
    raw = json.dumps(
        {
            "runs": runs,
            "contexto": context,
            "policy_version_id": policy_id,
            "policy_content_hash": policy_content_hash,
            "shadow_logic_version": SHADOW_LOGIC_VERSION,
            "data_referencia": ref.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _policy_content_hash(
    parametros: dict[str, Any], regras: list[dict[str, Any]],
) -> str:
    """Fingerprint policy content independently of its persisted version id."""

    def rule_key(rule: dict[str, Any]) -> tuple[int, int | str, str]:
        try:
            return (0, int(rule.get("ordem")), str(rule.get("id") or ""))
        except (TypeError, ValueError):
            return (1, str(rule.get("ordem") or ""), str(rule.get("id") or ""))

    raw = json.dumps(
        {
            "parametros": parametros,
            "regras": sorted(regras, key=rule_key),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _persist_shadow(database, payload: dict[str, Any]) -> None:
    """Append one idempotent shadow result without the loaded policy envelope."""
    database.table("policy_shadow_runs").upsert(
        {key: value for key, value in payload.items() if key != "policy_version"},
        on_conflict="operation_id,policy_version_id,entrada_hash",
        ignore_duplicates=True,
    ).execute()


def avaliar_sombra(
    operation_id: str,
    *,
    database=None,
    aplicar: bool = False,
    versao: int | None = None,
) -> dict[str, Any]:
    if database is None:
        from app.core.database import supabase

        database = supabase
    policy = load_policy(database=database, versao=versao, status="SOMBRA")
    policy_content_hash = _policy_content_hash(
        policy["parametros"], policy["regras"]
    )
    entrada = montar_entrada(operation_id, database=database)
    official, ref = _official(database, operation_id)
    insumos_posteriores = (
        _input_components_after_score(database, operation_id)
        if official is not None
        else []
    )
    if official is not None:
        official = {**official, "_insumos_posteriores": insumos_posteriores}
    _set_pct_max_contrato(entrada.operation, official)
    result = avaliar(entrada, policy["parametros"], policy["regras"], ref)
    divergencias = []
    if official:
        computed = result.as_dict()
        for field in _FIELDS:
            policy_value = computed.get(field)
            if field not in official:
                divergencias.append(
                    _non_comparable(
                        field, policy_value, None, "campo ausente no snapshot oficial"
                    )
                )
                continue
            official_value = official[field]
            if field == "ajuste_pd":
                adjusted = _ajuste_pd_values(policy_value, official_value)
                if adjusted is None:
                    divergencias.append(
                        _non_comparable(
                            field,
                            policy_value,
                            official_value,
                            "formato antigo de ajuste_pd sem anos completos",
                        )
                    )
                    continue
                policy_value, official_value = adjusted
            if field == "bloqueios":
                policy_value = sorted(
                    {_block_category(item) for item in (policy_value or [])}
                )
                official_value = sorted(
                    {_block_category(item) for item in (official_value or [])}
                )
                different = policy_value != official_value
            elif isinstance(policy_value, (int, float)) and isinstance(
                official_value, (int, float)
            ):
                # Regularity factors are already rounded to two decimals; a
                # 0.96 -> 0.91 certificate haircut is material even though
                # binary floating point can make it appear just below 0.05.
                different = (
                    abs(policy_value - official_value) > 0.000001
                    if field in {"fator_regularidade", "fator_potencial"}
                    else abs(policy_value - official_value) > 0.05
                )
            else:
                different = policy_value != official_value
            if different:
                divergencias.append(
                    {
                        "campo": field,
                        "valor_politica": policy_value,
                        "valor_oficial": official_value,
                        "diferenca": None,
                        "classe": "PARIDADE",
                        "motivo": "valor divergente",
                    }
                )
    partial_run = any(
        str(run.get("status") or "").upper() == "PARCIAL"
        for run in entrada.runs_usados.values()
    )
    parity_divergencias = [
        item for item in divergencias if item["classe"] == "PARIDADE"
    ]
    temporal_certificate_input = bool(
        official
        and _is_temporal_certificate_input(
            entrada, official, computed, ref, parity_divergencias
        )
    )
    if parity_divergencias and insumos_posteriores:
        for divergence in parity_divergencias:
            divergence.update(
                {
                    "classe": "INSUMO_POSTERIOR",
                    "motivo": "insumos refeitos apos o score oficial",
                    "componentes_posteriores": insumos_posteriores,
                }
            )
    if temporal_certificate_input and not insumos_posteriores:
        for divergence in parity_divergencias:
            divergence.update(
                {
                    "classe": "INSUMO_TEMPORAL",
                    "motivo": "certidao vencida apos a decisao oficial",
                }
            )
    if (
        _missing_parity_specialists(entrada.runs_usados, official)
        or partial_run
        or official is None
        or (parity_divergencias and insumos_posteriores)
    ):
        classe = "SEM_DADOS"
    elif not parity_divergencias:
        classe = "IGUAL"
    elif temporal_certificate_input:
        classe = "INSUMO_TEMPORAL"
    elif _effects_explain(
        parity_divergencias,
        result.efeitos_novos,
        getattr(result, "limite_multiplicadores", []),
    ):
        classe = "ESPERADA"
    else:
        classe = "INESPERADA"
    payload = {
        "operation_id": operation_id,
        "ambiente": entrada.operation.get("ambiente") or "PRODUCAO",
        "policy_version_id": policy["version"]["id"],
        "policy_version": policy["version"],
        "entrada_hash": _hash(
            entrada.runs_usados,
            entrada.operation,
            str(policy["version"]["id"]),
            policy_content_hash,
            ref,
        ),
        "data_referencia": ref.isoformat(),
        "runs_usados": entrada.runs_usados,
        "resultado": {
            **result.as_dict(),
            "shadow_logic_version": SHADOW_LOGIC_VERSION,
        },
        "oficial": official or {},
        "divergencias": divergencias,
        "classe_geral": classe,
        "parametros_divergentes": [],
    }
    if aplicar:
        _persist_shadow(database, payload)
    return payload


def _comparative_projection(result: dict[str, Any]) -> dict[str, Any]:
    """Keeps the comparative shadow response deliberately small and stable."""
    value = result.get("resultado") or {}
    return {
        "versao": (result.get("policy_version") or {}).get("versao"),
        "rating": value.get("rating"),
        "limite": value.get("limite_aprovado_rs"),
        "vetos": value.get("bloqueios") or [],
        "condicoes_precedentes": value.get("condicoes_precedentes") or [],
        "alcada_minima": value.get("alcada_minima", 1),
        "taxa_ajuste_pp": value.get("taxa_ajuste_pp", 0.0),
    }


def _rule_identity(rule: dict[str, Any]) -> tuple[str, str, str | None]:
    return (
        str(rule.get("codigo") or ""),
        str(rule.get("classe") or ""),
        str(rule["parametro_alvo"]) if rule.get("parametro_alvo") is not None else None,
    )


def _fired_rule_identities(result: dict[str, Any]) -> set[tuple[str, str, str | None]]:
    """Project the engine trail portions that demonstrate a generic rule fired."""
    fired: set[tuple[str, str, str | None]] = set()
    for code in result.get("bloqueios") or []:
        fired.add((str(code), "VETO", None))
    for item in result.get("condicoes_precedentes") or []:
        if isinstance(item, dict) and item.get("codigo"):
            fired.add((str(item["codigo"]), "CONDICAO", "cp"))
    for item in result.get("alcada_motivos") or []:
        if isinstance(item, dict) and item.get("codigo"):
            fired.add((str(item["codigo"]), "AJUSTE", "alcada"))
    for item in result.get("limite_multiplicadores") or []:
        if isinstance(item, dict) and item.get("codigo"):
            fired.add((str(item["codigo"]), "AJUSTE", "limite_mult"))
    return fired


def _comparative_rule_class(
    diff: dict[str, Any], primary_rules: list[dict[str, Any]],
    reference_rules: list[dict[str, Any]], primary_result: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Classify policy-to-policy deltas without changing official parity class."""
    reference_identities = {_rule_identity(rule) for rule in reference_rules}
    seen: set[tuple[str, str, str | None]] = set()
    new_identities: list[tuple[str, str, str | None]] = []
    for rule in primary_rules:
        identity = _rule_identity(rule)
        if identity not in reference_identities and identity not in seen:
            new_identities.append(identity)
            seen.add(identity)
    fired = _fired_rule_identities(primary_result)
    rules_new = [
        {
            "codigo": code,
            "classe": classe,
            "parametro_alvo": target,
            "disparou": identity in fired,
        }
        for identity in new_identities
        for code, classe, target in [identity]
    ]
    if not diff:
        return "IGUAL", rules_new

    def explains(field: str, identity: tuple[str, str, str | None]) -> bool:
        _code, classe, target = identity
        if field in {"vetos", "rating"}:
            return classe == "VETO"
        if field == "limite":
            return classe == "VETO" or (classe == "AJUSTE" and target == "limite_mult")
        if field == "condicoes_precedentes":
            return classe == "CONDICAO" and target == "cp"
        if field == "alcada_minima":
            return classe == "AJUSTE" and target == "alcada"
        return False

    fired_new = set(new_identities) & fired
    return (
        "ESPERADA" if all(any(explains(field, identity) for identity in fired_new) for field in diff) else "INESPERADA",
        rules_new,
    )


def comparar_sombra(
    operation_id: str,
    *,
    versao: int,
    comparar_com: int,
    database=None,
    aplicar: bool = False,
) -> dict[str, Any]:
    """Runs a draft policy in shadow and compares its decision projection."""
    if database is None:
        from app.core.database import supabase

        database = supabase
    primary_policy = load_policy(database=database, versao=versao, status="SOMBRA")
    reference_policy = load_policy(database=database, versao=comparar_com, status="SOMBRA")
    primary = avaliar_sombra(
        operation_id,
        database=database,
        aplicar=False,
        versao=versao,
    )
    reference = avaliar_sombra(
        operation_id,
        database=database,
        aplicar=False,
        versao=comparar_com,
    )
    projected_primary = _comparative_projection(primary)
    projected_reference = _comparative_projection(reference)
    diff = {
        key: {
            "versao": projected_primary[key],
            "comparar_com": projected_reference[key],
        }
        for key in projected_primary
        if key != "versao" and projected_primary[key] != projected_reference[key]
    }
    classe_comparativo, regras_novas = _comparative_rule_class(
        diff,
        primary_policy["regras"],
        reference_policy["regras"],
        primary.get("resultado") or {},
    )
    primary["comparativo"] = {
        "comparar_com": comparar_com,
        "diff": diff,
        "regras_novas": regras_novas,
    }
    primary["classe_comparativo"] = classe_comparativo
    if aplicar:
        _persist_shadow(database, primary)
    return {
        "operation_id": operation_id,
        "versao": projected_primary,
        "comparar_com": projected_reference,
        "diff": diff,
        "shadow": primary,
    }
