from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

router = APIRouter()

ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
MAX_FILE_SIZE_MB = 10


@router.get("/pending")
async def list_pending_uploads(operation_id: Optional[str] = None):
    """List pending tasks or all upload progress for one operation."""
    from app.services.upload_service import UploadService

    svc = UploadService()
    return await svc.list_pending(operation_id=operation_id)


@router.post("/{token}")
async def receive_upload(
    token: str,
    file: UploadFile = File(...),
    document_type: str = Form(...),
):
    """Receive one certificate and resume automatically after the last upload."""
    from app.services.storage_service import StorageService
    from app.services.upload_service import UploadService
    from app.workers.tasks.orchestrator import resume_after_upload

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

    storage_svc = StorageService()
    storage_key = await storage_svc.upload_document(
        operation_id=task["operation_id"],
        document_type=task["document_type"],
        content=content,
        filename=file.filename,
        mime_type=file.content_type,
    )

    await upload_svc.complete(
        task_id=task["id"],
        operation_id=task["operation_id"],
        storage_key=storage_key,
        filename=file.filename,
        mime_type=file.content_type,
        file_size=len(content),
    )

    still_pending = await upload_svc.count_pending(task["operation_id"])
    if still_pending == 0:
        resume_after_upload.delay(task["operation_id"])

    return {
        "status": "uploaded",
        "operation_id": task["operation_id"],
        "pipeline_resumed": still_pending == 0,
        "uploads_remaining": still_pending,
    }
