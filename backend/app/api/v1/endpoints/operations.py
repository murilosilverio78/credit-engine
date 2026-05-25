from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, field_validator
from typing import Optional
import re

router = APIRouter()


class PropostaInput(BaseModel):
    cnpj: str
    valor_solicitado: Optional[float] = None
    contrato_id: Optional[str] = None
    prazo_dias: Optional[int] = None
    source: str = "frontend_mvp"

    @field_validator("cnpj")
    @classmethod
    def validate_cnpj(cls, v):
        digits = re.sub(r"\D", "", v)
        if len(digits) != 14:
            raise ValueError("CNPJ deve ter 14 dígitos")
        return digits


@router.post("/", status_code=201)
async def create_operation(payload: PropostaInput, background_tasks: BackgroundTasks):
    """
    Cria uma nova operação de crédito e inicia o pipeline de análise.
    Retorna imediatamente com o operation_id; análise roda em background.
    """
    from app.services.operation_service import OperationService
    from app.workers.tasks.orchestrator import start_analysis

    svc = OperationService()
    operation = await svc.create(
        cnpj=payload.cnpj,
        valor_solicitado=payload.valor_solicitado,
        contrato_id=payload.contrato_id,
        prazo_dias=payload.prazo_dias,
        source=payload.source,
    )

    # Dispara pipeline assíncrono
    start_analysis.delay(str(operation["id"]))

    return {
        "operation_id": operation["id"],
        "cnpj": payload.cnpj,
        "status": "pending",
        "message": "Análise iniciada. Acompanhe via /api/v1/operations/{id}",
    }


@router.get("/{operation_id}")
async def get_operation(operation_id: str):
    """Retorna o status completo de uma operação, incluindo snapshots por componente."""
    from app.services.operation_service import OperationService

    svc = OperationService()
    operation = await svc.get_with_snapshots(operation_id)

    if not operation:
        raise HTTPException(status_code=404, detail="Operação não encontrada")

    return operation


@router.get("/")
async def list_operations(
    status: Optional[str] = None,
    cnpj: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
):
    """Lista operações com filtros. Usado pelo Admin UI."""
    from app.services.operation_service import OperationService

    svc = OperationService()
    return await svc.list(status=status, cnpj=cnpj, limit=limit, offset=offset)
