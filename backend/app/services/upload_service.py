"""
UploadService: gerencia tarefas de upload manual (human-in-the-loop).
"""
from typing import Optional
from datetime import datetime, timezone
from app.core.database import supabase
from app.services.audit_service import AuditService
import structlog

logger = structlog.get_logger()

audit = AuditService()


class UploadService:

    async def list_pending(self) -> list[dict]:
        """Lista todas as tarefas de upload pendentes com dados da operação."""
        result = supabase.table("upload_tasks")\
            .select("*, operations(cnpj, razao_social)")\
            .eq("status", "pending")\
            .order("created_at", desc=False)\
            .execute()
        return result.data

    async def get_by_token(self, token: str) -> Optional[dict]:
        """Busca task pelo token único de upload."""
        try:
            result = supabase.table("upload_tasks")\
                .select("*")\
                .eq("token", token)\
                .single()\
                .execute()
            return result.data
        except Exception:
            return None

    async def create(self, operation_id: str, document_type: str) -> dict:
        """Cria task de upload pendente e retorna o token."""
        from app.core.config import settings
        from datetime import timedelta

        expires_at = (
            datetime.now(timezone.utc) +
            timedelta(hours=settings.UPLOAD_TASK_EXPIRY_HOURS)
        ).isoformat()

        result = supabase.table("upload_tasks").insert({
            "operation_id": operation_id,
            "document_type": document_type,
            "status": "pending",
            "expires_at": expires_at,
        }).execute()

        task = result.data[0]
        audit.log(
            operation_id=operation_id,
            action="upload_requested",
            payload={"document_type": document_type, "token": task["token"]},
        )

        logger.info("upload_task.created", operation_id=operation_id, document_type=document_type)
        return task

    async def complete(
        self,
        task_id: str,
        operation_id: str,
        storage_key: str,
        filename: str,
        mime_type: str,
        file_size: int,
    ):
        """Marca task como completada e persiste metadados do documento."""
        # Atualiza task
        supabase.table("upload_tasks").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", task_id).execute()

        # Busca document_type da task
        task = supabase.table("upload_tasks")\
            .select("document_type")\
            .eq("id", task_id)\
            .single()\
            .execute()

        # Persiste documento
        supabase.table("documents").insert({
            "operation_id": operation_id,
            "document_type": task.data["document_type"],
            "storage_key": storage_key,
            "filename": filename,
            "mime_type": mime_type,
            "file_size_bytes": file_size,
            "upload_source": "manual",
        }).execute()

        # Atualiza snapshot do componente correspondente
        supabase.table("component_snapshots").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "parsed_result": {"storage_key": storage_key, "filename": filename},
        }).eq("operation_id", operation_id)\
          .eq("component", task.data["document_type"])\
          .execute()

        audit.log(
            operation_id=operation_id,
            action="upload_received",
            payload={"document_type": task.data["document_type"], "filename": filename},
        )

        logger.info("upload_task.completed", operation_id=operation_id, task_id=task_id)

    async def count_pending(self, operation_id: str) -> int:
        """Conta uploads ainda pendentes para uma operação."""
        result = supabase.table("upload_tasks")\
            .select("id", count="exact")\
            .eq("operation_id", operation_id)\
            .eq("status", "pending")\
            .execute()
        return result.count or 0
