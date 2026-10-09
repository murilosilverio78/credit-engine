"""Enrich quote-scoped operations with public Comprasnet contract data."""

from __future__ import annotations

from io import BytesIO
import math
import re
import statistics
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from pypdf import PdfReader
import structlog

from app.core.config import settings
from app.integrations.broadfactor.client import BroadfactorClient, Recebimento, parse_data
from app.workers.base import BaseComponentTask
from app.workers.base import _execute_snapshot_write as _execute_with_retry
from app.workers.http_utils import fetch_json_with_retry


logger = structlog.get_logger()

COMPRASNET_BASE_URL = "https://contratos.comprasnet.gov.br"
MAX_CONTRACT_PDF_BYTES = 10 * 1024 * 1024
MAX_CONTRACT_PDF_PAGES = 15
STRONG_UASG_SOURCES = {"CONTRATO_PDF", "CONTRATOS_NUMERO"}
SINGLE_PAYMENT_HIGH_VALUE = 100_000.0
MARGIN_CONTRACT_RATIO = 0.70
MARGIN_DIVERGENCE_TOLERANCE_PCT = 10.0


def _db(operation_id: str, action: str, request):
    return _execute_with_retry(operation_id, "contratos_comprasnet", action, request)


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return round(_number(value), 2)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    return parse_data(str(value)) if value not in (None, "") else None


def _plain_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).lower()


def _as_items(payload: list | dict | None) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "content", "results", "items"):
        nested = payload.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    if payload.get("error") and len(payload) <= 3:
        return []
    return [payload]


class ComprasnetClient:
    def __init__(
        self,
        base_url: str = COMPRASNET_BASE_URL,
        timeout: float = 45,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            verify=settings.HTTPX_VERIFY_SSL,
        )

    def get(
        self,
        path: str,
        *,
        timeout_seconds: float | None = None,
    ) -> list | dict:
        try:
            return fetch_json_with_retry(
                self._client,
                f"{self.base_url}{path}",
                portal_request=False,
                total_timeout_seconds=timeout_seconds,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _load_operation(operation_id: str) -> dict[str, Any]:
    from app.core.database import supabase

    result = _db(
        operation_id,
        "load_operation_context",
        lambda: supabase.table("operations")
        .select(
            "cnpj,cotacao_id,contrato_id,margem_disponivel,prazo_vincendo_meses,"
            "prazo_final_meses,prazo_vincendo_indisponivel,uasg,contrato_pncp_controle"
        )
        .eq("id", operation_id)
        .single()
        .execute(),
    )
    return result.data or {}


def _load_contracts_snapshot(operation_id: str) -> dict[str, Any]:
    from app.core.database import supabase

    result = _db(
        operation_id,
        "load_portal_contracts_snapshot",
        lambda: supabase.table("component_snapshots")
        .select("status,parsed_result")
        .eq("operation_id", operation_id)
        .eq("component", "contratos")
        .maybe_single()
        .execute(),
    )
    row = result.data or {}
    if row.get("status") != "completed":
        return {}
    return row.get("parsed_result") or {}


@dataclass(frozen=True)
class _UasgCandidate:
    codigo: str
    origem: str
    numero_preferido: str | None = None
    contrato_ativo: bool | None = None


def _normalize_contract_number(value: Any) -> str:
    text = str(value or "").strip()
    digits = _digits(text)
    if len(digits) == 9:
        return digits
    if "/" in text:
        sequence, year = text.rsplit("/", 1)
        sequence_digits = _digits(sequence)
        year_digits = _digits(year)
        if sequence_digits and len(year_digits) == 4:
            return f"{sequence_digits.zfill(5)}{year_digits}"
    if 5 <= len(digits) <= 8:
        return f"{digits[:-4].zfill(5)}{digits[-4:]}"
    return digits


def _ordered_uasgs(receipts: list[Recebimento]) -> list[str]:
    totals: dict[str, float] = defaultdict(float)
    for receipt in receipts:
        uasg = _digits(receipt.codigo_ug)
        if uasg:
            totals[uasg] += max(float(receipt.valor or 0), 0.0)
    return [uasg for uasg, _ in sorted(totals.items(), key=lambda item: -item[1])]


def _extract_uasgs_from_contract_pdf(content: bytes | None) -> list[str]:
    if not content or len(content) > MAX_CONTRACT_PDF_BYTES:
        return []
    try:
        reader = PdfReader(BytesIO(content))
    except Exception:
        return []

    found: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"(?:gest[aã]o\s*/\s*unidade|uasg)\s*(?::|-)?\s*(\d{6})",
        re.IGNORECASE,
    )
    for page in reader.pages[:MAX_CONTRACT_PDF_PAGES]:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        for match in pattern.findall(text):
            if match not in seen:
                found.append(match)
                seen.add(match)
    return found


def _contract_pdf_candidates(
    client: BroadfactorClient,
    cotacao_id: str,
    contracts: list[Any],
) -> list[_UasgCandidate]:
    download = getattr(client, "baixar_contrato", None)
    if not callable(download):
        return []

    candidates: list[_UasgCandidate] = []
    seen: set[str] = set()
    for contract in contracts:
        number = str(getattr(contract, "numero_contrato", "") or "")
        try:
            content = download(cotacao_id, number)
        except Exception as exc:
            logger.warning(
                "contratos_comprasnet.contract_pdf_unavailable",
                cotacao_id=cotacao_id,
                numero_contrato=number,
                error=str(exc),
            )
            continue
        for codigo in _extract_uasgs_from_contract_pdf(content):
            if codigo in seen:
                continue
            candidates.append(
                _UasgCandidate(
                    codigo=codigo,
                    origem="CONTRATO_PDF",
                    numero_preferido=_normalize_contract_number(number) or None,
                )
            )
            seen.add(codigo)
    return candidates


def _discover_uasg_candidates(
    contract_numbers: list[str],
    contracts_snapshot: dict[str, Any],
    receipts: list[Recebimento],
    *,
    pncp_candidate: _UasgCandidate | None = None,
    pdf_candidates: list[_UasgCandidate] | None = None,
    manual_uasg: str | None = None,
) -> list[_UasgCandidate]:
    expected_numbers = set(contract_numbers)
    exact: list[_UasgCandidate] = []
    remaining: list[_UasgCandidate] = []

    for item in contracts_snapshot.get("contratos_detalhe") or []:
        if not isinstance(item, dict):
            continue
        codigo = _digits(item.get("unidade_codigo"))
        if not codigo:
            continue
        number = _normalize_contract_number(item.get("numero"))
        candidate = _UasgCandidate(
            codigo=codigo,
            origem="CONTRATOS_NUMERO" if number in expected_numbers else "CONTRATOS_ATIVO",
            numero_preferido=number if number in expected_numbers else None,
            contrato_ativo=bool(item.get("ativo")),
        )
        if number in expected_numbers:
            exact.append(candidate)
        else:
            remaining.append(candidate)

    # When the Portal tied number and UASG together, do not dilute that signal
    # with unrelated Portal contracts. Received UASGs remain the last resort.
    portal_candidates = exact or sorted(
        remaining,
        key=lambda candidate: candidate.contrato_ativo is not True,
    )

    candidates: list[_UasgCandidate] = []
    seen: set[str] = set()
    if pncp_candidate:
        candidates.append(pncp_candidate)
        seen.add(pncp_candidate.codigo)
    manual_code = _digits(manual_uasg)
    if len(manual_code) == 6:
        candidates.append(_UasgCandidate(manual_code, "MANUAL"))
        seen.add(manual_code)
    for candidate in pdf_candidates or []:
        if candidate.codigo not in seen:
            candidates.append(candidate)
            seen.add(candidate.codigo)
    for candidate in portal_candidates:
        if candidate.codigo not in seen:
            candidates.append(candidate)
            seen.add(candidate.codigo)
    for codigo in _ordered_uasgs(receipts):
        if codigo not in seen:
            candidates.append(_UasgCandidate(codigo=codigo, origem="RECEBIDO"))
            seen.add(codigo)
    return candidates


def _supplier_cnpj(contract: dict[str, Any]) -> str:
    supplier = contract.get("fornecedor")
    if isinstance(supplier, dict):
        nested = supplier.get("cnpj_cpf_idgener") or supplier.get("cnpj")
        if nested:
            return _digits(nested)
    return _digits(
        contract.get("fonecedor_cnpj_cpf_idgener")
        or contract.get("fornecedor_cnpj_cpf_idgener")
        or contract.get("cnpj_fornecedor")
    )


def _contract_number(contract: dict[str, Any]) -> str:
    return _digits(contract.get("numero") or contract.get("numero_contrato"))


def _contract_matches(
    contract: dict[str, Any],
    cnpj: str,
    contract_numbers: set[str] | None = None,
) -> bool:
    if _supplier_cnpj(contract) != _digits(cnpj):
        return False
    return not contract_numbers or _contract_number(contract) in contract_numbers


def _find_contract(
    client: ComprasnetClient,
    cnpj: str,
    contract_numbers: list[str],
    uasgs: list[_UasgCandidate],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    attempted_uasgs: list[str] = []
    source_error = False
    truncated_reason: str | None = None
    max_uasgs = max(int(settings.COMPRASNET_MAX_UASGS), 0)
    timeout_seconds = max(float(settings.COMPRASNET_BUSCA_TIMEOUT_SECONDS), 0.0)
    interval_seconds = max(float(settings.COMPRASNET_UASG_INTERVAL_SECONDS), 0.0)
    started_at = time.monotonic()

    def remaining_search_time() -> float:
        return max(timeout_seconds - (time.monotonic() - started_at), 0.0)

    for index, uasg in enumerate(uasgs):
        if index >= max_uasgs:
            truncated_reason = "limite_uasgs"
            break
        if time.monotonic() - started_at >= timeout_seconds:
            truncated_reason = "limite_tempo"
            break
        attempted_uasgs.append(uasg.codigo)
        numbers = (
            [uasg.numero_preferido]
            if uasg.numero_preferido
            else contract_numbers
        )
        for number in numbers:
            if time.monotonic() - started_at >= timeout_seconds:
                truncated_reason = "limite_tempo"
                break
            try:
                payload = client.get(
                    f"/api/contrato/ugorigem/{uasg.codigo}/numeroano/{number}",
                    timeout_seconds=remaining_search_time(),
                )
                candidates = _as_items(payload)
                attempts.append(
                    {
                        "tipo": "DIRETA",
                        "uasg": uasg.codigo,
                        "origem": uasg.origem,
                        "contrato_ativo": uasg.contrato_ativo,
                        "numero": number,
                        "resultados": len(candidates),
                    }
                )
                for candidate in candidates:
                    if _contract_matches(candidate, cnpj):
                        return candidate, {
                            "busca": "DIRETA",
                            "tentativas": attempts,
                            "fallback_usado": False,
                            "busca_exaustiva": False,
                            "busca_truncada": False,
                            "motivo_truncamento": None,
                            "erro_fonte": source_error,
                            "uasgs_tentadas": attempted_uasgs,
                        }
            except Exception as exc:
                source_error = True
                attempts.append(
                    {
                        "tipo": "DIRETA",
                        "uasg": uasg.codigo,
                        "origem": uasg.origem,
                        "contrato_ativo": uasg.contrato_ativo,
                        "numero": number,
                        "erro": str(exc)[:300],
                    }
                )
                logger.warning(
                    "contratos_comprasnet.direct_lookup_failed",
                    uasg=uasg.codigo,
                    origem_uasg=uasg.origem,
                    numero=number,
                    error=str(exc),
                )
        if truncated_reason:
            break
        if index + 1 < min(len(uasgs), max_uasgs) and interval_seconds:
            time.sleep(interval_seconds)

    fallback_uasg = next(
        (candidate for candidate in uasgs if candidate.origem == "CONTRATOS_NUMERO"),
        None,
    )
    if fallback_uasg and not truncated_reason:
        try:
            payload = client.get(
                f"/api/contrato/ug/{fallback_uasg.codigo}",
                timeout_seconds=remaining_search_time(),
            )
            candidates = _as_items(payload)
            attempts.append(
                {
                    "tipo": "FALLBACK_UG",
                    "uasg": fallback_uasg.codigo,
                    "origem": fallback_uasg.origem,
                    "numero": fallback_uasg.numero_preferido,
                    "resultados": len(candidates),
                }
            )
            expected_numbers = set(contract_numbers)
            for candidate in candidates:
                if _contract_matches(candidate, cnpj, expected_numbers):
                    return candidate, {
                        "busca": "FALLBACK_UG",
                        "tentativas": attempts,
                        "fallback_usado": True,
                        "busca_exaustiva": False,
                        "busca_truncada": False,
                        "motivo_truncamento": None,
                        "erro_fonte": source_error,
                        "uasgs_tentadas": attempted_uasgs,
                    }
        except Exception as exc:
            source_error = True
            attempts.append(
                {
                    "tipo": "FALLBACK_UG",
                    "uasg": fallback_uasg.codigo,
                    "origem": fallback_uasg.origem,
                    "numero": fallback_uasg.numero_preferido,
                    "erro": str(exc)[:300],
                }
            )
            logger.warning(
                "contratos_comprasnet.fallback_lookup_failed",
                uasg=fallback_uasg.codigo,
                error=str(exc),
            )

    exhaustive = (
        truncated_reason is None
        and not source_error
        and len(attempted_uasgs) == len(uasgs)
    )
    return None, {
        "busca": None,
        "tentativas": attempts,
        "fallback_usado": fallback_uasg is not None,
        "busca_exaustiva": exhaustive,
        "busca_truncada": truncated_reason is not None,
        "motivo_truncamento": truncated_reason,
        "erro_fonte": source_error,
        "uasgs_tentadas": attempted_uasgs,
    }


def _nested_contract_party(contract: dict[str, Any]) -> tuple[dict, dict, dict]:
    contractor = contract.get("contratante") or {}
    origin = contractor.get("orgao_origem") or {}
    agency = origin.get("unidade_gestora_origem") or {}
    if not origin:
        origin = contractor.get("orgao") or {}
    if not agency:
        agency = origin.get("unidade_gestora") or {}
    return contractor, origin, agency


def _remaining_months(end: date | None, today: date) -> int | None:
    if not end:
        return None
    months = (end.year - today.year) * 12 + end.month - today.month
    if end.day < today.day:
        months -= 1
    return max(months, 0)


def _normalize_contract(
    contract: dict[str, Any],
    today: date,
) -> dict[str, Any]:
    _, organization, agency = _nested_contract_party(contract)
    end = _date(contract.get("vigencia_fim"))
    renewable = _plain_text(contract.get("prorrogavel"))
    return {
        "contrato_id": contract.get("id"),
        "numero": contract.get("numero"),
        "uasg": (
            str(agency.get("codigo")) if agency.get("codigo") is not None
            else str(contract.get("unidade_origem_codigo") or "") or None
        ),
        "uasg_nome": (
            agency.get("nome_resumido")
            or agency.get("nome")
            or contract.get("unidade_origem_nome")
            or contract.get("unidade_nome_resumido")
        ),
        "orgao": organization.get("nome") or contract.get("orgao_nome"),
        "vigencia_inicio": (
            _date(contract.get("vigencia_inicio")).isoformat()
            if _date(contract.get("vigencia_inicio")) else None
        ),
        "vigencia_fim": end.isoformat() if end else None,
        "prazo_vincendo_meses": _remaining_months(end, today),
        "valor_inicial": _optional_number(contract.get("valor_inicial")),
        "valor_global": _optional_number(contract.get("valor_global")),
        "valor_parcela": _optional_number(contract.get("valor_parcela")),
        "num_parcelas": _optional_int(contract.get("num_parcelas")),
        "situacao": contract.get("situacao"),
        "prorrogavel": (
            True if renewable == "sim" else False if renewable == "nao" else None
        ),
        "objeto": contract.get("objeto"),
        "fornecedor_cnpj": _supplier_cnpj(contract),
        "match_confianca": "CNPJ_CONFERIDO",
    }


def _empty_contract() -> dict[str, Any]:
    fields = (
        "contrato_id", "numero", "uasg", "uasg_nome", "orgao",
        "vigencia_inicio", "vigencia_fim", "prazo_vincendo_meses",
        "valor_inicial", "valor_global", "valor_parcela", "num_parcelas",
        "situacao", "prorrogavel", "objeto", "fornecedor_cnpj",
    )
    return {**{field: None for field in fields}, "match_confianca": "SEM_MATCH"}


def _lag_days(start: Any, end: Any) -> int | None:
    start_date = _date(start)
    end_date = _date(end)
    if not start_date or not end_date:
        return None
    lag = (end_date - start_date).days
    return lag if lag >= 0 else None


def _distribution(values: list[int]) -> tuple[float | None, int | None, int | None]:
    if not values:
        return None, None, None
    ordered = sorted(values)
    p90_index = max(math.ceil(len(ordered) * 0.90) - 1, 0)
    return (
        round(float(statistics.median(ordered)), 2),
        ordered[p90_index],
        ordered[-1],
    )


def _invoice_pending(invoice: dict[str, Any]) -> bool:
    status = _plain_text(invoice.get("situacao"))
    if any(term in status for term in ("pendente", "aguardando")):
        return True
    settled = any(term in status for term in ("pago", "paga", "liquidado", "apropriado"))
    return not invoice.get("data_liquidacao") and not settled


def _performance(invoices: list[dict[str, Any]]) -> dict[str, Any]:
    billed = round(sum(_number(item.get("valor")) for item in invoices), 2)
    disallowed = round(sum(_number(item.get("glosa")) for item in invoices), 2)
    ateste_lags = [
        lag for item in invoices
        if (lag := _lag_days(item.get("emissao"), item.get("ateste"))) is not None
    ]
    liquidation_lags = [
        lag for item in invoices
        if (lag := _lag_days(
            item.get("ateste") or item.get("emissao"),
            item.get("data_liquidacao"),
        )) is not None
    ]
    ateste_median, ateste_p90, ateste_max = _distribution(ateste_lags)
    liquid_median, liquid_p90, liquid_max = _distribution(liquidation_lags)
    pending = [item for item in invoices if _invoice_pending(item)]
    issue_dates = sorted(
        parsed for item in invoices
        if (parsed := _date(item.get("emissao"))) is not None
    )
    return {
        "n_faturas": len(invoices),
        "faturado_total": billed,
        "glosa_total": disallowed,
        "taxa_glosa": round(disallowed / billed, 6) if billed else None,
        "lag_ateste_mediana": ateste_median,
        "lag_ateste_p90": ateste_p90,
        "lag_ateste_max": ateste_max,
        "lag_liquidacao_mediana": liquid_median,
        "lag_liquidacao_p90": liquid_p90,
        "lag_liquidacao_max": liquid_max,
        "faturas_pendentes": len(pending),
        "valor_pendente": round(
            sum(
                _number(item.get("valorliquido"))
                or max(_number(item.get("valor")) - _number(item.get("glosa")), 0)
                for item in pending
            ),
            2,
        ),
        "primeira_fatura": issue_dates[0].isoformat() if issue_dates else None,
        "ultima_fatura": issue_dates[-1].isoformat() if issue_dates else None,
    }


def _commitments(items: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "empenhado_total": round(sum(_number(item.get("empenhado")) for item in items), 2),
        "liquidado_total": round(sum(_number(item.get("liquidado")) for item in items), 2),
        "pago_total": round(sum(_number(item.get("pago")) for item in items), 2),
        "aliquidar_total": round(sum(_number(item.get("aliquidar")) for item in items), 2),
    }


def _measurement_rhythm(invoices: list[dict[str, Any]]) -> dict[str, Any]:
    months: dict[tuple[int, int], float] = defaultdict(float)
    for invoice in invoices:
        issued = _date(invoice.get("emissao"))
        if issued:
            months[(issued.year, issued.month)] += _number(invoice.get("valor"))
    if not months:
        return {"meses_com_fatura": 0, "meses_sem_fatura": 0,
                "valor_medio_mensal": 0.0}
    first = min(year * 12 + month for year, month in months)
    last = max(year * 12 + month for year, month in months)
    span = last - first + 1
    return {
        "meses_com_fatura": len(months),
        "meses_sem_fatura": max(span - len(months), 0),
        "valor_medio_mensal": round(sum(months.values()) / span, 2),
    }


def _predictability(contract: dict[str, Any]) -> str:
    installments = contract.get("num_parcelas")
    installment_value = contract.get("valor_parcela") or 0
    total_value = contract.get("valor_global") or 0
    if installments is not None and installments > 1 and installment_value > 0:
        return "ALTA"
    if installments == 1 and total_value >= SINGLE_PAYMENT_HIGH_VALUE:
        return "BAIXA"
    return "INDETERMINADA"


def _margin_consistency(
    contract_value: float | None,
    paid_total: float,
    available_margin: Any,
) -> tuple[dict[str, Any], list[str]]:
    margin = _optional_number(available_margin)
    if contract_value is None or margin is None:
        return {
            "margem_calculada": None,
            "margem_disponivel": margin,
            "divergencia_pct": None,
            "status": "INDETERMINADA",
        }, []
    calculated = round(MARGIN_CONTRACT_RATIO * (contract_value - paid_total), 2)
    if margin == 0:
        divergence = 0.0 if calculated == 0 else None
    else:
        divergence = round(abs(calculated - margin) / abs(margin) * 100, 2)
    if divergence is None:
        status = "INDETERMINADA"
        flags: list[str] = []
    elif divergence <= MARGIN_DIVERGENCE_TOLERANCE_PCT:
        status = "CONSISTENTE"
        flags = ["margem_consistente_com_comprasnet"]
    else:
        status = "DIVERGENTE"
        flags = ["margem_divergente_com_comprasnet"]
    return {
        "margem_calculada": calculated,
        "margem_disponivel": margin,
        "divergencia_pct": divergence,
        "status": status,
    }, flags


def _failure(
    reason: str,
    origem_numero_contrato: str | None = None,
    *,
    status_consulta: str = "NAO_VERIFICADO",
    **details: Any,
) -> dict[str, Any]:
    return {
        "status_consulta": status_consulta,
        "motivo": reason,
        "origem_numero_contrato": origem_numero_contrato,
        "contrato_comprasnet": _empty_contract(),
        "performance_contratual": _performance([]),
        "empenhos": _commitments([]),
        "previsibilidade_fluxo": "INDETERMINADA",
        "ritmo_medicao": None,
        "fonte_prazo_vincendo": None,
        "fonte_valor_global": None,
        "consistencia_margem": {
            "margem_calculada": None,
            "margem_disponivel": None,
            "divergencia_pct": None,
            "status": "INDETERMINADA",
        },
        "flags": [reason],
        **details,
    }


def _fetch(
    cnpj: str,
    operation_id: str | None = None,
    today: date | None = None,
    broadfactor_client: BroadfactorClient | None = None,
    comprasnet_client: ComprasnetClient | None = None,
) -> dict[str, Any]:
    if not operation_id:
        return _failure("operation_id_ausente")

    today = today or date.today()
    operation = _load_operation(operation_id)
    cotacao_id = operation.get("cotacao_id")
    contract_number_source: str | None = None
    numbers: list[str] = []
    receipts: list[Recebimento] = []
    pdf_candidates: list[_UasgCandidate] = []
    source_errors: list[str] = []

    if not cotacao_id:
        number = _normalize_contract_number(operation.get("contrato_id"))
        if len(number) != 9:
            return _failure("cotacao_id_ausente")
        numbers = [number]
        contract_number_source = "OPERACAO_MANUAL"
    else:
        contract_number_source = "BROADFACTOR"

    own_broadfactor = bool(cotacao_id and broadfactor_client is None)
    own_comprasnet = comprasnet_client is None
    if cotacao_id and broadfactor_client is None:
        broadfactor_client = BroadfactorClient()
    comprasnet_client = comprasnet_client or ComprasnetClient()
    try:
        if cotacao_id:
            contracts = broadfactor_client.contratos_da_cotacao(cotacao_id)
            for item in contracts:
                number = _digits(item.numero_contrato)
                if len(number) == 9 and number not in numbers:
                    numbers.append(number)
            if not numbers:
                return _failure(
                    "numero_contrato_indisponivel",
                    origem_numero_contrato=contract_number_source,
                    cotacao_id=cotacao_id,
                )
            pdf_candidates = _contract_pdf_candidates(
                broadfactor_client,
                cotacao_id,
                contracts,
            )

        contracts_snapshot = _load_contracts_snapshot(operation_id)
        pncp_code = _digits(operation.get("uasg"))
        pncp_candidate = None
        if operation.get("contrato_pncp_controle") and len(pncp_code) == 6:
            pncp_candidate = _UasgCandidate(
                codigo=pncp_code,
                origem="PNCP",
                numero_preferido=numbers[0] if numbers else None,
            )
        if cotacao_id:
            try:
                receipts = broadfactor_client.recebimentos(
                    cotacao_id,
                    paginas=1,
                    tamanho=50,
                )
            except Exception as exc:
                receipts = []
                source_errors.append("recebimentos_broadfactor")
                logger.warning(
                    "contratos_comprasnet.receipts_fallback_unavailable",
                    operation_id=operation_id,
                    cotacao_id=cotacao_id,
                    error=str(exc),
                )
        uasgs = _discover_uasg_candidates(
            numbers,
            contracts_snapshot,
            receipts,
            pncp_candidate=pncp_candidate,
            pdf_candidates=pdf_candidates,
            manual_uasg=operation.get("uasg"),
        )
        candidate_sources = [
            {
                "uasg": candidate.codigo,
                "origem": candidate.origem,
                "numero_preferido": candidate.numero_preferido,
            }
            for candidate in uasgs
        ]
        if not uasgs:
            return _failure(
                "uasg_indisponivel",
                origem_numero_contrato=contract_number_source,
                cotacao_id=cotacao_id,
                numeros_contrato=numbers,
                uasgs_tentadas=[],
                busca_exaustiva=False,
                fontes_candidatos=[],
                erros_fonte=source_errors,
            )

        raw_contract, search = _find_contract(
            comprasnet_client,
            cnpj,
            numbers,
            uasgs,
        )
        if raw_contract is None:
            source_failed = bool(source_errors) or bool(search.get("erro_fonte"))
            has_strong_source = any(
                candidate.origem in STRONG_UASG_SOURCES for candidate in uasgs
            )
            confirmed = (
                bool(search.get("busca_exaustiva"))
                and has_strong_source
                and not source_failed
            )
            if confirmed:
                reason = "contrato_sem_match_cnpj"
                status_consulta = "NAO_ENCONTRADO_CONFIRMADO"
            elif source_failed:
                reason = "falha_consulta_comprasnet"
                status_consulta = "NAO_VERIFICADO"
            elif search.get("busca_truncada"):
                reason = "busca_truncada"
                status_consulta = "NAO_VERIFICADO"
            else:
                reason = "fonte_forte_indisponivel"
                status_consulta = "NAO_VERIFICADO"
            return _failure(
                reason,
                origem_numero_contrato=contract_number_source,
                status_consulta=status_consulta,
                cotacao_id=cotacao_id,
                numeros_contrato=numbers,
                uasgs_tentadas=[
                    {
                        "uasg": code,
                        "origem": next(
                            candidate.origem
                            for candidate in uasgs
                            if candidate.codigo == code
                        ),
                    }
                    for code in search.get("uasgs_tentadas") or []
                ],
                busca_exaustiva=bool(search.get("busca_exaustiva")),
                fontes_candidatos=candidate_sources,
                erros_fonte=source_errors,
                diagnostico_busca=search,
            )

        contract = _normalize_contract(raw_contract, today)
        contract_id = contract.get("contrato_id")
        invoices: list[dict[str, Any]] = []
        commitments: list[dict[str, Any]] = []
        enrichment_errors: list[dict[str, str]] = []
        for name, path in (
            ("faturas", f"/api/contrato/{contract_id}/faturas"),
            ("empenhos", f"/api/contrato/{contract_id}/empenhos"),
        ):
            try:
                items = _as_items(comprasnet_client.get(path))
                if name == "faturas":
                    invoices = items
                else:
                    commitments = items
            except Exception as exc:
                enrichment_errors.append({"endpoint": name, "erro": str(exc)[:300]})
                logger.warning(
                    "contratos_comprasnet.enrichment_failed",
                    operation_id=operation_id,
                    contrato_id=contract_id,
                    endpoint=name,
                    error=str(exc),
                )

        performance = _performance(invoices)
        commitment_metrics = _commitments(commitments)
        predictability = _predictability(contract)
        consistency, flags = _margin_consistency(
            contract.get("valor_global"),
            commitment_metrics["pago_total"],
            operation.get("margem_disponivel"),
        )
        if enrichment_errors:
            flags.append("enriquecimento_comprasnet_parcial")

        result = {
            "status_consulta": "ENCONTRADO",
            "motivo": None,
            "cotacao_id": cotacao_id,
            "origem_numero_contrato": contract_number_source,
            "contrato_comprasnet": contract,
            "performance_contratual": performance,
            "empenhos": commitment_metrics,
            "previsibilidade_fluxo": predictability,
            "ritmo_medicao": (
                _measurement_rhythm(invoices) if predictability == "BAIXA" else None
            ),
            "fonte_prazo_vincendo": "COMPRASNET",
            "fonte_valor_global": "COMPRASNET",
            "consistencia_margem": consistency,
            "uasgs_tentadas": [
                {
                    "uasg": code,
                    "origem": next(
                        candidate.origem
                        for candidate in uasgs
                        if candidate.codigo == code
                    ),
                }
                for code in search.get("uasgs_tentadas") or []
            ],
            "busca_exaustiva": False,
            "fontes_candidatos": candidate_sources,
            "diagnostico_busca": search,
            "erros_enriquecimento": enrichment_errors,
            "flags": flags,
        }
        logger.info(
            "contratos_comprasnet.completed",
            operation_id=operation_id,
            cotacao_id=cotacao_id,
            contrato_id=contract_id,
            busca=search["busca"],
            n_faturas=performance["n_faturas"],
            divergencia_margem_pct=consistency["divergencia_pct"],
        )
        return result
    except Exception as exc:
        logger.exception(
            "contratos_comprasnet.unavailable",
            operation_id=operation_id,
            cotacao_id=cotacao_id,
            error=str(exc),
        )
        return _failure(
            "falha_consulta_comprasnet",
            origem_numero_contrato=contract_number_source,
            cotacao_id=cotacao_id,
            error=str(exc)[:300],
        )
    finally:
        if own_comprasnet:
            comprasnet_client.close()
        if own_broadfactor and broadfactor_client is not None:
            close = getattr(getattr(broadfactor_client, "_s", None), "close", None)
            if callable(close):
                close()


def _resolved_contract_sources(
    operation: dict[str, Any],
    comprasnet: dict[str, Any],
    extraction: dict[str, Any],
    default_term: int,
) -> dict[str, Any]:
    contract = comprasnet.get("contrato_comprasnet") or {}
    comprasnet_match = contract.get("match_confianca") == "CNPJ_CONFERIDO"

    comprasnet_term = contract.get("prazo_vincendo_meses") if comprasnet_match else None
    extraction_term = extraction.get("prazo_vincendo_meses")
    if comprasnet_term is not None:
        remaining_term = int(comprasnet_term)
        term_source = "COMPRASNET"
    elif extraction_term is not None:
        remaining_term = int(extraction_term)
        term_source = "EXTRACAO_LLM"
    else:
        remaining_term = None
        term_source = "DEFAULT"

    comprasnet_value = contract.get("valor_global") if comprasnet_match else None
    extraction_value = extraction.get("valor_global")
    if comprasnet_value is not None:
        contract_value = round(float(comprasnet_value), 2)
        value_source = "COMPRASNET"
    elif extraction_value is not None:
        contract_value = round(float(extraction_value), 2)
        value_source = "EXTRACAO_LLM"
    else:
        contract_value = None
        value_source = None

    final_term = min(default_term, remaining_term) if remaining_term is not None else default_term
    return {
        "prazo_vincendo_meses": remaining_term,
        "prazo_final_meses": final_term,
        "prazo_vincendo_indisponivel": remaining_term is None,
        "fonte_prazo_vincendo": term_source,
        "valor_global_contrato": contract_value,
        "fonte_valor_global": value_source,
    }


def apply_contract_source_precedence(operation_id: str) -> dict[str, Any]:
    """Persist deterministic source precedence after both phase-2 tasks finish."""
    from app.core.database import supabase

    operation_result = _db(
        operation_id,
        "load_operation_for_source_precedence",
        lambda: supabase.table("operations")
        .select("prazo_final_meses")
        .eq("id", operation_id)
        .single()
        .execute(),
    )
    snapshots_result = _db(
        operation_id,
        "load_contract_snapshots_for_precedence",
        lambda: supabase.table("component_snapshots")
        .select("component,status,parsed_result")
        .eq("operation_id", operation_id)
        .in_("component", ["contratos_comprasnet", "contrato_extracao"])
        .execute(),
    )
    snapshots = {
        row.get("component"): row.get("parsed_result") or {}
        for row in (snapshots_result.data or [])
        if row.get("status") == "completed"
    }
    operation = operation_result.data or {}
    try:
        from app.services.eligibility_params_service import get_eligibility_config

        default_term = int(get_eligibility_config()["prazo_padrao_meses"])
    except Exception as exc:
        default_term = int(operation.get("prazo_final_meses") or 12)
        logger.warning(
            "contratos_comprasnet.eligibility_params_fallback",
            operation_id=operation_id,
            prazo_padrao_meses=default_term,
            error=str(exc),
        )

    resolved = _resolved_contract_sources(
        operation,
        snapshots.get("contratos_comprasnet", {}),
        snapshots.get("contrato_extracao", {}),
        default_term,
    )
    _db(
        operation_id,
        "save_contract_source_precedence",
        lambda: supabase.table("operations")
        .update(resolved)
        .eq("id", operation_id)
        .execute(),
    )

    comprasnet_snapshot = snapshots.get("contratos_comprasnet")
    if comprasnet_snapshot:
        parsed_result = {
            **comprasnet_snapshot,
            "fonte_prazo_vincendo": resolved["fonte_prazo_vincendo"],
            "fonte_valor_global": resolved["fonte_valor_global"],
            "prazo_final_meses": resolved["prazo_final_meses"],
            "valor_global_resolvido": resolved["valor_global_contrato"],
        }
        _db(
            operation_id,
            "save_contract_sources_in_snapshot",
            lambda: supabase.table("component_snapshots")
            .update({"parsed_result": parsed_result})
            .eq("operation_id", operation_id)
            .eq("component", "contratos_comprasnet")
            .execute(),
        )
    return resolved


_task = BaseComponentTask()


def run_contratos_comprasnet(operation_id: str):
    return _task.execute(
        operation_id,
        component="contratos_comprasnet",
        handler=_fetch,
        use_cache=False,
    )
