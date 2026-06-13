from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
import structlog

from app.core.auth import get_current_user
from app.core.database import supabase

router = APIRouter()
logger = structlog.get_logger()

# Limite de tempo para considerar uma operação "pending" como órfã (minutos)
ORPHAN_THRESHOLD_MINUTES = 30


@router.get("/operations")
async def list_operations():
    """Lista operações para o admin."""
    from app.services.operation_service import OperationService
    svc = OperationService()
    return await svc.list()


@router.get("/upload-tasks")
async def list_upload_tasks():
    """Lista tarefas de upload pendentes."""
    from app.services.upload_service import UploadService
    svc = UploadService()
    return await svc.list_pending()


@router.get("/orphans")
async def list_orphan_operations(
    current_user: dict = Depends(get_current_user),
):
    """Lista operações que seriam consideradas órfãs, sem alterá-las."""
    if current_user.get("role") != "diretor":
        raise HTTPException(status_code=403, detail="Acesso negado")

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=ORPHAN_THRESHOLD_MINUTES)).isoformat()
    orphans = supabase.table("operations")\
        .select("id, cnpj, created_at, status")\
        .eq("status", "pending")\
        .lt("created_at", cutoff)\
        .order("created_at", desc=False)\
        .execute()
    return {"count": len(orphans.data or []), "operations": orphans.data or []}


@router.post("/cleanup-orphans")
async def cleanup_orphan_operations(
    current_user: dict = Depends(get_current_user),
):
    """
    Marca como 'failed' operações travadas em 'pending' há mais de
    ORPHAN_THRESHOLD_MINUTES minutos. Cobre o caso de o processo FastAPI
    ter reiniciado durante uma análise (task em background perdida).

    NÃO afeta:
      - operações 'waiting_upload' (têm prazo próprio de 48h via upload_tasks)
      - operações já 'completed', 'approved', 'rejected', 'escalated', 'failed'
    Somente diretor pode executar.
    """
    if current_user.get("role") != "diretor":
        raise HTTPException(status_code=403, detail="Somente diretor pode executar limpeza")

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=ORPHAN_THRESHOLD_MINUTES)).isoformat()

    # Buscar operações órfãs: pending criadas antes do cutoff
    orphans = supabase.table("operations")\
        .select("id, cnpj, created_at, status")\
        .eq("status", "pending")\
        .lt("created_at", cutoff)\
        .execute()

    orphan_rows = orphans.data or []
    if not orphan_rows:
        return {"cleaned": 0, "operations": []}

    orphan_ids = [row["id"] for row in orphan_rows]

    # Marcar como failed
    supabase.table("operations")\
        .update({
            "status": "failed",
            "error_message": "Opera??o ?rf? - processo reiniciado (cleanup manual)",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })\
        .in_("id", orphan_ids)\
        .execute()

    # Marcar componentes ainda pendentes/running dessas operações como failed
    supabase.table("component_snapshots")\
        .update({"status": "failed", "error_message": "Operação órfã - processo reiniciado"})\
        .in_("operation_id", orphan_ids)\
        .in_("status", ["pending", "running"])\
        .execute()

    for row in orphan_rows:
        logger.warning(
            "operation.orphan_cleaned",
            operation_id=row["id"],
            cnpj=row.get("cnpj"),
            created_at=row.get("created_at"),
        )

    return {
        "cleaned": len(orphan_ids),
        "operations": [
            {"id": r["id"], "cnpj": r.get("cnpj"), "created_at": r.get("created_at")}
            for r in orphan_rows
        ],
    }
