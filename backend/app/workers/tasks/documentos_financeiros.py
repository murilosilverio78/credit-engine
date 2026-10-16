"""Componente pago de leitura de documentos financeiros do cliente."""
from __future__ import annotations

from app.services.cache_service import CacheService
from app.services.documentos_financeiros import extract_documentos_financeiros
from app.services.snapshot_service import SnapshotService
from app.core.database import supabase
from app.workers.base import BaseComponentTask

_task = BaseComponentTask()


def run_documentos_financeiros(operation_id: str, use_cache: bool = True):
    # O handler sempre devolve o manifest. Antes de usar o cache, comparamos o
    # manifest atual para impedir que um PDF novo espere o TTL de 24 horas.
    cnpj = SnapshotService().get_cnpj(operation_id)
    supabase.table("component_snapshots").upsert(
        {"operation_id": operation_id, "component": "documentos_financeiros", "status": "pending"},
        on_conflict="operation_id,component", ignore_duplicates=True,
    ).execute()
    rows = (
        supabase.table("documentos_cliente").select("sha256").eq("cnpj", cnpj)
        .eq("status", "CAPTURADO").not_.is_("sha256", "null").execute().data or []
    )
    import hashlib
    manifest = hashlib.sha256("|".join(sorted(str(row["sha256"]) for row in rows)).encode()).hexdigest()
    cache = CacheService()
    cached = cache.get(cnpj, "documentos_financeiros") if use_cache else None
    if cached and cached.get("manifest_documentos") != manifest:
        cache.invalidate(cnpj, "documentos_financeiros")
    return _task.execute(operation_id, "documentos_financeiros", extract_documentos_financeiros, use_cache=use_cache)
