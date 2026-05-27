"""UploadService: manages human-in-the-loop upload tasks."""
import base64
from datetime import datetime, timezone
import json
import re
from typing import Optional

import anthropic
import structlog

from app.core.database import supabase
from app.services.audit_service import AuditService
from app.utils.encoding import fix_dict_encoding

logger = structlog.get_logger()
audit = AuditService()


def _normalize_cnpj(cnpj: Optional[str]) -> str:
    return re.sub(r"\D", "", cnpj or "")


def _parse_json_response(text: str) -> dict:
    clean = re.sub(r"```json|```", "", text).strip()
    return json.loads(clean)


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

        task = supabase.table("upload_tasks")\
            .select("document_type,file_content")\
            .eq("id", task_id)\
            .single()\
            .execute()
        document_type = task.data["document_type"]
        parsed_result = {"storage_key": storage_key, "filename": filename}
        snapshot_status = "completed"
        error_message = None

        try:
            extraction = fix_dict_encoding(self._extract_certificate(
                document_type=document_type,
                file_content=task.data.get("file_content"),
            ))
            parsed_result.update({
                "status": "obtida",
                "resultado": extraction.get("resultado"),
                "cnpj_certidao": extraction.get("cnpj"),
                "data_emissao": extraction.get("data_emissao"),
                "data_validade": extraction.get("data_validade"),
                "valida": extraction.get("valida"),
                "orgao_emissor": extraction.get("orgao_emissor"),
                "numero_certidao": extraction.get("numero_certidao"),
            })

            operation = supabase.table("operations")\
                .select("cnpj")\
                .eq("id", operation_id)\
                .single()\
                .execute()
            operation_cnpj = _normalize_cnpj(operation.data.get("cnpj"))
            certificate_cnpj = _normalize_cnpj(extraction.get("cnpj"))
            if certificate_cnpj and operation_cnpj and certificate_cnpj != operation_cnpj:
                snapshot_status = "failed"
                error_message = (
                    f"CNPJ da certidão ({certificate_cnpj}) não corresponde ao "
                    f"CNPJ analisado ({operation_cnpj})"
                )
                logger.warning(
                    "upload_task.cnpj_mismatch",
                    operation_id=operation_id,
                    task_id=task_id,
                    certificate_cnpj=certificate_cnpj,
                    operation_cnpj=operation_cnpj,
                )
        except Exception as exc:
            logger.warning(
                "upload_task.certificate_extraction_failed",
                operation_id=operation_id,
                task_id=task_id,
                document_type=document_type,
                error=str(exc),
            )

        supabase.table("upload_tasks").update({
            "status": "completed",
            "completed_at": completed_at,
        }).eq("id", task_id).execute()

        supabase.table("documents").insert({
            "operation_id": operation_id,
            "document_type": document_type,
            "storage_key": storage_key,
            "filename": filename,
            "mime_type": mime_type,
            "file_size_bytes": file_size,
            "upload_source": "manual",
        }).execute()
        snapshot_update = {
            "status": snapshot_status,
            "completed_at": completed_at,
            "parsed_result": parsed_result,
            "error_message": error_message,
        }
        supabase.table("component_snapshots").update(snapshot_update)\
          .eq("operation_id", operation_id)\
          .eq("component", document_type)\
          .execute()

        audit.log(
            operation_id=operation_id,
            action="upload_received",
            payload={
                "document_type": document_type,
                "filename": filename,
                "snapshot_status": snapshot_status,
            },
        )
        logger.info(
            "upload_task.completed",
            operation_id=operation_id,
            task_id=task_id,
            snapshot_status=snapshot_status,
        )

    def _extract_certificate(self, document_type: str, file_content: Optional[str]) -> dict:
        """Extract certificate metadata from the stored PDF through Claude."""
        if not file_content:
            raise ValueError("Conteúdo da certidão não foi persistido")

        from app.core.config import settings

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": file_content,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"""Analise esta certidão e extraia as informações.
Retorne APENAS um JSON válido, sem markdown:
{{
  "tipo_certidao": "cnd_federal|cndt_tst|fgts",
  "resultado": "negativa|positiva|positiva_com_efeitos_negativa",
  "cnpj": "CNPJ encontrado na certidão (só números)",
  "data_emissao": "DD/MM/AAAA",
  "data_validade": "DD/MM/AAAA ou null",
  "orgao_emissor": "nome do órgão emissor",
  "numero_certidao": "número/código da certidão se houver",
  "valida": true/false (true se resultado=negativa e dentro da validade)
}}
O tipo esperado para este upload é "{document_type}".
Se não conseguir extrair algum campo, use null.""",
                    },
                ],
            }],
        )
        result_text = next(
            block.text for block in response.content if block.type == "text"
        )
        return _parse_json_response(result_text)

    async def store_inline_content(self, task_id: str, content: bytes):
        """Persist certificate bytes used for extraction and inline fallback."""
        encoded = base64.b64encode(content).decode("ascii")
        supabase.table("upload_tasks").update({
            "file_content": encoded,
        }).eq("id", task_id).execute()
        logger.info("upload_task.inline_content_stored", task_id=task_id)

    async def reset(self, task: dict):
        """Return an uploaded certificate to pending state for replacement."""
        supabase.table("upload_tasks").update({
            "status": "pending",
            "completed_at": None,
            "file_content": None,
        }).eq("id", task["id"]).execute()
        supabase.table("component_snapshots").update({
            "status": "waiting_upload",
            "completed_at": None,
            "parsed_result": None,
            "error_message": None,
        }).eq("operation_id", task["operation_id"])\
          .eq("component", task["document_type"])\
          .execute()
        logger.info(
            "upload_task.reset",
            operation_id=task["operation_id"],
            task_id=task["id"],
        )

    async def incomplete_document_types(self, operation_id: str) -> list[str]:
        """List document types that still block explicit pipeline resume."""
        result = supabase.table("upload_tasks")\
            .select("document_type,status")\
            .eq("operation_id", operation_id)\
            .neq("status", "completed")\
            .execute()
        incomplete = [task["document_type"] for task in result.data]
        failed = supabase.table("component_snapshots")\
            .select("component")\
            .eq("operation_id", operation_id)\
            .in_("component", ["cnd_federal", "cndt_tst", "fgts"])\
            .eq("status", "failed")\
            .execute()
        incomplete.extend(snapshot["component"] for snapshot in failed.data)
        return list(dict.fromkeys(incomplete))

    async def count_pending(self, operation_id: str) -> int:
        """Count incomplete uploads required by an operation."""
        result = supabase.table("upload_tasks")\
            .select("id", count="exact")\
            .eq("operation_id", operation_id)\
            .eq("status", "pending")\
            .execute()
        return result.count or 0
