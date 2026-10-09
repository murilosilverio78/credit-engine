"""Cadastro empresarial com BrasilAPI e fallback público CNPJá Open."""
from __future__ import annotations

import os
import threading
import time
from typing import Any

import httpx
import structlog

from app.workers.base import BaseComponentTask

logger = structlog.get_logger()
BRASIL_API_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
CNPJA_OPEN_URL = "https://open.cnpja.com/office/{cnpj}"
SSL_VERIFY = os.getenv("SSL_VERIFY", "true").lower() != "false"
CNPJA_MIN_INTERVAL_SECONDS = 13.0
_cnpja_lock = threading.Lock()
_cnpja_last_call = 0.0
_monotonic = time.monotonic
_sleep = time.sleep
_task = BaseComponentTask()


def run_brasil_api(operation_id: str, *, use_cache: bool = True):
    return _task.execute(operation_id, component="brasil_api", handler=_fetch, use_cache=use_cache)


def _normalized_brasil_api(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "cnpj": data.get("cnpj"), "razao_social": data.get("razao_social"),
        "nome_fantasia": data.get("nome_fantasia"),
        "situacao_cadastral": data.get("descricao_situacao_cadastral"),
        "data_situacao": data.get("data_situacao_cadastral"),
        "data_abertura": data.get("data_inicio_atividade"),
        "natureza_juridica": data.get("natureza_juridica"), "porte": data.get("porte"),
        "capital_social": data.get("capital_social"),
        "atividade_principal": data.get("cnae_fiscal_descricao"),
        "cnae_fiscal": str(data.get("cnae_fiscal")) if data.get("cnae_fiscal") else None,
        "identificador_matriz_filial": data.get("identificador_matriz_filial"),
        "regime_tributario": [{"ano": row.get("ano"), "forma": row.get("forma_de_tributacao")} for row in (data.get("regime_tributario") or [])],
        "atividades_secundarias": [row.get("descricao") for row in (data.get("cnaes_secundarios") or [])],
        "qsa": [{"nome": row.get("nome_socio"), "qualificacao": row.get("qualificacao_socio"), "data_entrada": row.get("data_entrada_sociedade")} for row in (data.get("qsa") or [])],
        "municipio": data.get("municipio"), "uf": data.get("uf"), "email": data.get("email"),
        "telefone": data.get("ddd_telefone_1"), "opcao_simples": data.get("opcao_pelo_simples"),
        "opcao_mei": data.get("opcao_pelo_mei"), "fonte": "BRASIL_API", "simples_desde": None,
    }


def _normalize_cnpja(data: dict[str, Any]) -> dict[str, Any]:
    company = data.get("company") or {}
    size = company.get("size") or {}
    porte = {1: "MICRO EMPRESA", 3: "EMPRESA DE PEQUENO PORTE", 5: "DEMAIS"}.get(size.get("id"))
    main, address = data.get("mainActivity") or {}, data.get("address") or {}
    simples, simei = company.get("simples") or {}, company.get("simei") or {}
    phones, emails = data.get("phones") or [], data.get("emails") or []
    phone = phones[0] if phones else {}
    return {
        "cnpj": data.get("taxId"), "razao_social": company.get("name"), "nome_fantasia": data.get("alias"),
        "situacao_cadastral": str((data.get("status") or {}).get("text") or "").upper() or None,
        "data_situacao": data.get("statusDate"), "data_abertura": data.get("founded"),
        "natureza_juridica": (company.get("nature") or {}).get("text"),
        "porte": porte or (str(size.get("text")).upper() if size.get("text") else None),
        "capital_social": company.get("equity"), "atividade_principal": main.get("text"),
        "cnae_fiscal": str(main.get("id")) if main.get("id") is not None else None,
        "atividades_secundarias": [activity.get("text") for activity in (data.get("sideActivities") or [])],
        "identificador_matriz_filial": 1 if data.get("head") is True else 2,
        "qsa": [{"nome": (member.get("person") or {}).get("name"), "qualificacao": (member.get("role") or {}).get("text"), "data_entrada": member.get("since")} for member in (company.get("members") or [])],
        "municipio": str(address.get("city") or "").upper() or None, "uf": address.get("state"),
        "email": (emails[0] or {}).get("address") if emails else None,
        "telefone": f"{phone.get('area') or ''}{phone.get('number') or ''}" or None,
        "opcao_simples": simples.get("optant"), "opcao_mei": simei.get("optant"),
        "regime_tributario": [], "fonte": "CNPJA_OPEN", "simples_desde": simples.get("since"),
    }


def _require_identity(data: dict[str, Any]) -> dict[str, Any]:
    if not data.get("cnpj") or not data.get("razao_social"):
        raise ValueError("resposta sem cnpj ou razao_social")
    return data


def _throttled_cnpja_request(client: httpx.Client, cnpj: str):
    global _cnpja_last_call
    with _cnpja_lock:
        wait = CNPJA_MIN_INTERVAL_SECONDS - (_monotonic() - _cnpja_last_call)
        if wait > 0:
            _sleep(wait)
        _cnpja_last_call = _monotonic()
        return client.get(CNPJA_OPEN_URL.format(cnpj=cnpj))


def _cnpja_get(client: httpx.Client, cnpj: str) -> dict[str, Any]:
    response = _throttled_cnpja_request(client, cnpj)
    if response.status_code == 429:
        try:
            retry_after = min(float(response.headers.get("Retry-After", 0)), 30.0)
        except (TypeError, ValueError):
            retry_after = 0.0
        if retry_after > 0:
            _sleep(retry_after)
        response = _throttled_cnpja_request(client, cnpj)
    response.raise_for_status()
    return response.json()


def _fetch(cnpj: str) -> dict[str, Any]:
    normalized_cnpj = "".join(character for character in cnpj if character.isdigit())
    with httpx.Client(timeout=15, verify=SSL_VERIFY) as client:
        try:
            response = client.get(BRASIL_API_URL.format(cnpj=normalized_cnpj))
            response.raise_for_status()
            return _require_identity(_normalized_brasil_api(response.json()))
        except Exception as brasil_error:
            try:
                result = _require_identity(_normalize_cnpja(_cnpja_get(client, normalized_cnpj)))
                logger.info("brasil_api.fallback", cnpj=normalized_cnpj, brasil_api_cause=str(brasil_error), cnpja_result="completed")
                return result
            except Exception as cnpja_error:
                logger.warning("brasil_api.fallback", cnpj=normalized_cnpj, brasil_api_cause=str(brasil_error), cnpja_result="failed", cnpja_cause=str(cnpja_error))
                raise RuntimeError(f"BrasilAPI: {brasil_error}; CNPJá Open: {cnpja_error}") from cnpja_error
