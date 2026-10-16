"""Captura idempotente de anexos financeiros Broadfactor para o R2.

Este modulo preserva bytes enquanto a cotacao esta ativa. Nao extrai dados,
nao cria achados e nao participa de score, funil ou relatorio.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

import structlog

from app.integrations.broadfactor.client import (
    BroadfactorClient,
    QuotationInactiveError,
)

if TYPE_CHECKING:
    from app.services.storage_service import StorageService


logger = structlog.get_logger()

DEFAULT_CONFIG = {
    "tipos_capturados": {
        "ENDIVIDAMENTO_EMPRESA",
        "FATURAMENTO",
        "ULTIMO_BALANCO",
        "PENULTIMO_BALANCO",
        "DRE",
        "ULTIMO_IMPOSTO_RENDA",
        "CONTRATO_SOCIAL",
    },
    "tamanho_max_bytes": 25 * 1024 * 1024,
    "max_tentativas": 3,
}
_MIME_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/zip": "zip",
    "image/jpeg": "jpg",
    "image/png": "png",
}
_TERMINAL_SKIP_STATUSES = {"CAPTURADO", "INDISPONIVEL", "IGNORADO_TAMANHO"}


def _rows(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    data = getattr(result, "data", result)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return [data] if isinstance(data, dict) else []


def _first(result: Any) -> dict[str, Any] | None:
    rows = _rows(result)
    return rows[0] if rows else None


def _normalized_cnpj(cnpj: str) -> str:
    value = re.sub(r"\D", "", str(cnpj or ""))
    if len(value) != 14:
        raise ValueError("cnpj_invalido")
    return value


def _erro(exc: Exception) -> str:
    """Nunca persiste ou registra corpo/base64/token vindo de fonte externa."""
    code = str(getattr(exc, "code", None) or "falha_na_captura")
    status = getattr(exc, "status", None)
    detail = f"{exc.__class__.__name__}:{code}:{status}"
    error_type = getattr(exc, "error_type", None)
    if error_type:
        detail = f"{detail}:{error_type}"
    return detail[:200]


def _extension(nome: str | None, mime: str | None) -> str:
    suffix = PurePath(nome or "").suffix.lower().lstrip(".")
    if suffix and re.fullmatch(r"[a-z0-9]{1,10}", suffix):
        return suffix
    return _MIME_EXTENSIONS.get((mime or "").lower(), "bin")


def _load_config(database: Any) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    try:
        row = _first(
            database.table("documentos_captura_config")
            .select("tipos_capturados,tamanho_max_bytes,max_tentativas")
            .eq("singleton", True)
            .maybe_single()
            .execute()
        )
    except Exception:
        # A migration e aplicada manualmente; manter defaults evita que a
        # captura torne a ingestao indisponivel durante uma janela de deploy.
        return config
    if not row:
        return config
    tipos = row.get("tipos_capturados")
    if isinstance(tipos, list):
        config["tipos_capturados"] = {str(value).upper() for value in tipos}
    for key in ("tamanho_max_bytes", "max_tentativas"):
        try:
            value = int(row.get(key))
            if value > 0:
                config[key] = value
        except (TypeError, ValueError):
            pass
    return config


def _existing_document(database: Any, cotacao_id: str, documento_id: str) -> dict[str, Any] | None:
    return _first(
        database.table("documentos_cliente")
        .select("id,status,tentativas,storage_key")
        .eq("cotacao_id", cotacao_id)
        .eq("broadfactor_documento_id", documento_id)
        .maybe_single()
        .execute()
    )


def _captured_by_hash(database: Any, cnpj: str, sha256: str) -> dict[str, Any] | None:
    return _first(
        database.table("documentos_cliente")
        .select("storage_key")
        .eq("cnpj", cnpj)
        .eq("sha256", sha256)
        .eq("status", "CAPTURADO")
        .limit(1)
        .execute()
    )


def _upsert(database: Any, payload: dict[str, Any]) -> None:
    database.table("documentos_cliente").upsert(
        payload,
        on_conflict="cotacao_id,broadfactor_documento_id",
    ).execute()


def _metadata(
    *, cotacao_id: str, cnpj: str, operation_id: str | None, document: Any
) -> dict[str, Any]:
    dono = str(getattr(document, "dono", None) or "EMPRESA")
    return {
        "cnpj": cnpj,
        "cotacao_id": cotacao_id,
        "operation_id": operation_id,
        "broadfactor_documento_id": str(getattr(document, "id", "") or ""),
        "tipo": str(getattr(document, "tipo", "") or "").upper(),
        "dono": dono,
        "dono_tipo": "EMPRESA" if dono == "EMPRESA" else "SOCIO",
    }


def _mark_indisponiveis(
    database: Any,
    documents: list[Any],
    *,
    cotacao_id: str,
    cnpj: str,
    operation_id: str | None,
    config: dict[str, Any],
) -> int:
    marked = 0
    for document in documents:
        if str(getattr(document, "tipo", "") or "").upper() not in config["tipos_capturados"]:
            continue
        document_id = str(getattr(document, "id", "") or "")
        if not document_id:
            continue
        existing = _existing_document(database, cotacao_id, document_id)
        if existing and str(existing.get("status") or "") == "CAPTURADO":
            continue
        payload = {
            **_metadata(
                cotacao_id=cotacao_id,
                cnpj=cnpj,
                operation_id=operation_id,
                document=document,
            ),
            "status": "INDISPONIVEL",
            "erro": "cotacao_inativa",
            "tentativas": int((existing or {}).get("tentativas") or 0),
        }
        _upsert(database, payload)
        marked += 1
    return marked


def capturar_documentos_cotacao(
    cotacao_id: str,
    cnpj: str,
    operation_id: str | None = None,
    *,
    database: Any = None,
    broadfactor_client: BroadfactorClient | None = None,
    storage: "StorageService | None" = None,
    config: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Captura documentos financeiros de uma cotacao ativa de forma idempotente."""
    if database is None:
        from app.core.database import supabase

        database = supabase
    cnpj = _normalized_cnpj(cnpj)
    effective_config = dict(config or _load_config(database))
    effective_config["tipos_capturados"] = {
        str(item).upper() for item in effective_config["tipos_capturados"]
    }
    client = broadfactor_client or BroadfactorClient()
    if storage is None:
        from app.services.storage_service import StorageService

        storage = StorageService()
    summary = {
        "capturados": 0,
        "reaproveitados": 0,
        "falhas": 0,
        "indisponiveis": 0,
        "ignorados": 0,
    }

    try:
        catalog = client.documentos_da_cotacao(cotacao_id)
    except QuotationInactiveError:
        # Sem catalogo atual, so ha como sinalizar tentativas ja conhecidas.
        database.table("documentos_cliente").update({
            "status": "INDISPONIVEL", "erro": "cotacao_inativa",
        }).eq("cotacao_id", cotacao_id).neq("status", "CAPTURADO").execute()
        summary["indisponiveis"] += 1
        return summary

    candidates = [
        document
        for document in catalog
        if str(getattr(document, "tipo", "") or "").upper()
        in effective_config["tipos_capturados"]
        and getattr(document, "id", None)
    ]

    for index, document in enumerate(candidates):
        document_id = str(document.id)
        existing = _existing_document(database, cotacao_id, document_id)
        status = str((existing or {}).get("status") or "")
        attempts = int((existing or {}).get("tentativas") or 0)
        if status in _TERMINAL_SKIP_STATUSES or attempts >= effective_config["max_tentativas"]:
            continue
        base = _metadata(
            cotacao_id=cotacao_id,
            cnpj=cnpj,
            operation_id=operation_id,
            document=document,
        )
        try:
            downloaded = client.baixar_documento(document_id)
            size = len(downloaded.conteudo)
            if size > effective_config["tamanho_max_bytes"]:
                _upsert(database, {
                    **base,
                    "nome_arquivo": downloaded.nome,
                    "mime": downloaded.mime,
                    "tamanho_bytes": size,
                    "status": "IGNORADO_TAMANHO",
                    "erro": "tamanho_maximo_excedido",
                    "tentativas": attempts,
                })
                summary["ignorados"] += 1
                continue

            digest = hashlib.sha256(downloaded.conteudo).hexdigest()
            ext = _extension(downloaded.nome, downloaded.mime)
            storage_key = f"clientes/{cnpj}/documentos/{digest}.{ext}"
            duplicate = _captured_by_hash(database, cnpj, digest)
            if duplicate and duplicate.get("storage_key"):
                storage_key = str(duplicate["storage_key"])
                summary["reaproveitados"] += 1
            else:
                uploaded = storage.upload_document_at_key(
                    storage_key,
                    downloaded.conteudo,
                    downloaded.mime or "application/octet-stream",
                )
                if uploaded:
                    summary["capturados"] += 1
                else:
                    summary["reaproveitados"] += 1

            _upsert(database, {
                **base,
                "nome_arquivo": downloaded.nome,
                "mime": downloaded.mime,
                "tamanho_bytes": size,
                "sha256": digest,
                "storage_key": storage_key,
                "status": "CAPTURADO",
                "erro": None,
                "tentativas": attempts,
                "capturado_em": datetime.now(timezone.utc).isoformat(),
            })
        except QuotationInactiveError:
            summary["indisponiveis"] += _mark_indisponiveis(
                database,
                candidates[index:],
                cotacao_id=cotacao_id,
                cnpj=cnpj,
                operation_id=operation_id,
                config=effective_config,
            )
            break
        except Exception as exc:
            _upsert(database, {
                **base,
                "status": "FALHA",
                "erro": _erro(exc),
                "tentativas": attempts + 1,
            })
            summary["falhas"] += 1
            logger.warning(
                "documentos_captura.document_failed",
                cotacao_id=cotacao_id,
                documento_id=document_id,
                error_type=exc.__class__.__name__,
            )

    return summary
