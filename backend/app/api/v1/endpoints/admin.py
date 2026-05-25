from fastapi import APIRouter

router = APIRouter()


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
