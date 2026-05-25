from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from typing import Literal

router = APIRouter()

ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
MAX_FILE_SIZE_MB = 10


@router.get("/pending")
async def list_pending_uploads():
    """Lista todas as tarefas de upload pendentes. Usado pelo Admin UI."""
    from app.services.upload_service import UploadService
    svc = UploadService()
    return await svc.list_pending()


@router.post("/{token}")
async def receive_upload(
    token: str,
    file: UploadFile = File(...),
    document_type: str = Form(...),
):
    """
    Recebe o upload de uma certidão via token único.
    Após o upload, verifica se todos os uploads pendentes da operação foram resolvidos
    e retoma o pipeline automaticamente.
    """
    from app.services.upload_service import UploadService
    from app.services.storage_service import StorageService
    from app.workers.tasks.orchestrator import resume_after_upload

    # Valida token
    upload_svc = UploadService()
    task = await upload_svc.get_by_token(token)

    if not task:
        raise HTTPException(status_code=404, detail="Token inválido ou expirado")

    if task["status"] != "pending":
        raise HTTPException(status_code=409, detail="Upload já realizado")

    # Valida arquivo
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Tipo de arquivo não permitido (PDF, JPEG ou PNG)")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Arquivo excede {MAX_FILE_SIZE_MB}MB")

    # Salva no R2
    storage_svc = StorageService()
    storage_key = await storage_svc.upload_document(
        operation_id=task["operation_id"],
        document_type=document_type,
        content=content,
        filename=file.filename,
        mime_type=file.content_type,
    )

    # Marca task como completada
    await upload_svc.complete(
        task_id=task["id"],
        operation_id=task["operation_id"],
        storage_key=storage_key,
        filename=file.filename,
        mime_type=file.content_type,
        file_size=len(content),
    )

    # Verifica se ainda há uploads pendentes
    still_pending = await upload_svc.count_pending(task["operation_id"])

    if still_pending == 0:
        # Retoma o pipeline
        resume_after_upload.delay(task["operation_id"])

    return {
        "status": "uploaded",
        "operation_id": task["operation_id"],
        "pipeline_resumed": still_pending == 0,
        "uploads_remaining": still_pending,
    }
