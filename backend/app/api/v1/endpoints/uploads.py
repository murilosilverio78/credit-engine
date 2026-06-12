from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
import structlog

router = APIRouter()
public_router = APIRouter()
logger = structlog.get_logger()

ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
MAX_FILE_SIZE_MB = 10


@router.get("/pending")
async def list_pending_uploads(operation_id: Optional[str] = None):
    """List pending tasks or all upload progress for one operation."""
    from app.services.upload_service import UploadService

    return await UploadService().list_pending(operation_id=operation_id)


@router.post("/operations/{operation_id}/resume")
async def resume_operation(operation_id: str, background_tasks: BackgroundTasks):
    """Resume the pipeline only after every manual certificate is uploaded."""
    from app.services.upload_service import UploadService
    from app.workers.tasks.orchestrator import resume_after_upload

    upload_svc = UploadService()
    tasks = await upload_svc.list_pending(operation_id=operation_id)
    if not tasks:
        raise HTTPException(status_code=400, detail="Nenhuma certidão configurada")

    pending = await upload_svc.incomplete_document_types(operation_id)
    if pending:
        raise HTTPException(
            status_code=400,
            detail=f"Certidões pendentes: {pending}",
        )

    background_tasks.add_task(resume_after_upload, operation_id)
    return {"operation_id": operation_id, "status": "resume_requested"}


@public_router.delete("/{token}", dependencies=[])
async def remove_upload(token: str):
    """Reset one uploaded task so the certificate can be replaced."""
    from app.services.upload_service import UploadService

    upload_svc = UploadService()
    task = await upload_svc.get_by_token(token)
    if not task:
        raise HTTPException(status_code=404, detail="Token invalido ou expirado")
    if task["status"] not in ("completed", "failed"):
        raise HTTPException(status_code=409, detail="Upload ainda nao concluido")

    await upload_svc.reset(task)
    return {"operation_id": task["operation_id"], "status": "pending"}


@public_router.post("/{token}", dependencies=[])
async def receive_upload(
    token: str,
    file: UploadFile = File(...),
    document_type: str = Form(...),
):
    """Receive one certificate and hold it until explicit pipeline resume."""
    from app.services.storage_service import StorageService
    from app.services.upload_service import UploadService

    upload_svc = UploadService()
    task = await upload_svc.get_by_token(token)

    if not task:
        raise HTTPException(status_code=404, detail="Token invalido ou expirado")
    if task["status"] != "pending":
        raise HTTPException(status_code=409, detail="Upload ja realizado")
    if document_type != task["document_type"]:
        raise HTTPException(
            status_code=400,
            detail="Tipo de documento nao corresponde ao token",
        )
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Tipo de arquivo nao permitido (PDF, JPEG ou PNG)",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"Arquivo excede {MAX_FILE_SIZE_MB}MB",
        )

    storage_backend = "r2"
    try:
        storage_key = await StorageService().upload_document(
            operation_id=task["operation_id"],
            document_type=task["document_type"],
            content=content,
            filename=file.filename,
            mime_type=file.content_type,
        )
    except Exception as exc:
        storage_backend = "supabase_inline"
        storage_key = f"upload_tasks/{task['id']}/file_content"
        logger.warning(
            "storage.inline_fallback",
            operation_id=task["operation_id"],
            task_id=task["id"],
            error=str(exc),
        )

    await upload_svc.store_inline_content(task["id"], content)
    await upload_svc.complete(
        task_id=task["id"],
        operation_id=task["operation_id"],
        storage_key=storage_key,
        filename=file.filename,
        mime_type=file.content_type,
        file_size=len(content),
    )

    still_pending = await upload_svc.count_pending(task["operation_id"])
    return {
        "status": "uploaded",
        "operation_id": task["operation_id"],
        "pipeline_resumed": False,
        "uploads_remaining": still_pending,
        "storage_backend": storage_backend,
    }
