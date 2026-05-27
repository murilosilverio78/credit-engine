"""UploadService: manages human-in-the-loop upload tasks."""
import base64
from datetime import datetime, timezone
from typing import Optional

import structlog

from app.core.database import supabase
from app.services.audit_service import AuditService

logger = structlog.get_logger()
audit = AuditService()


class UploadService:
    async def list_pending(self, operation_id: Optional[str] = None) -> list[dict]:
        """List the pending queue or full upload progress for one operation."""
        query = supabase.table("upload_tasks")\
            .select(
                "id,operation_id,document_type,token,status,notified_at,"
                "completed_at,expires_at,created_at,operations(cnpj, razao_social)"
            )
        if operation_id:
            query = query.eq("operation_id", operation_id)
        else:
            query = query.eq("status", "pending")
        return query.order("created_at", desc=False).execute().data

    async def get_by_token(self, token: str) -> Optional[dict]:
        """Look up an upload task through its unique token."""
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
        """Create a pending task and return its upload token."""
        from datetime import timedelta
        from app.core.config import settings

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
        logger.info(
            "upload_task.created",
            operation_id=operation_id,
            document_type=document_type,
        )
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
        """Complete one task and persist document metadata."""
        completed_at = datetime.now(timezone.utc).isoformat()
        supabase.table("upload_tasks").update({
            "status": "completed",
            "completed_at": completed_at,
        }).eq("id", task_id).execute()

        task = supabase.table("upload_tasks")\
            .select("document_type")\
            .eq("id", task_id)\
            .single()\
            .execute()
        document_type = task.data["document_type"]

        supabase.table("documents").insert({
            "operation_id": operation_id,
            "document_type": document_type,
            "storage_key": storage_key,
            "filename": filename,
            "mime_type": mime_type,
            "file_size_bytes": file_size,
            "upload_source": "manual",
        }).execute()
        supabase.table("component_snapshots").update({
            "status": "completed",
            "completed_at": completed_at,
            "parsed_result": {"storage_key": storage_key, "filename": filename},
        }).eq("operation_id", operation_id)\
          .eq("component", document_type)\
          .execute()

        audit.log(
            operation_id=operation_id,
            action="upload_received",
            payload={"document_type": document_type, "filename": filename},
        )
        logger.info("upload_task.completed", operation_id=operation_id, task_id=task_id)

    async def store_inline_content(self, task_id: str, content: bytes):
        """Persist a certificate in Supabase when object storage is unavailable."""
        encoded = base64.b64encode(content).decode("ascii")
        supabase.table("upload_tasks").update({
            "file_content": encoded,
        }).eq("id", task_id).execute()
        logger.info("upload_task.inline_content_stored", task_id=task_id)

    async def count_pending(self, operation_id: str) -> int:
        """Count incomplete uploads required by an operation."""
        result = supabase.table("upload_tasks")\
            .select("id", count="exact")\
            .eq("operation_id", operation_id)\
            .eq("status", "pending")\
            .execute()
        return result.count or 0
