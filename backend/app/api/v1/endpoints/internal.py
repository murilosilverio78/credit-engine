import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.workers.tasks.broadfactor_ingestao import run_broadfactor_ingestao


router = APIRouter()


def verify_internal_token(
    x_internal_token: Annotated[
        str | None,
        Header(alias="X-Internal-Token"),
    ] = None,
) -> None:
    configured_token = settings.INTERNAL_JOB_TOKEN
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail="Job interno nao configurado: INTERNAL_JOB_TOKEN esta vazio.",
        )
    if x_internal_token is None or not secrets.compare_digest(
        x_internal_token,
        configured_token,
    ):
        raise HTTPException(status_code=401, detail="Token interno invalido.")


@router.post("/ingestao/broadfactor")
async def trigger_broadfactor_ingestion(
    background_tasks: BackgroundTasks,
    dry_run: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=100),
    _: None = Depends(verify_internal_token),
):
    if dry_run:
        return await run_broadfactor_ingestao(dry_run=True, limit=limit)

    background_tasks.add_task(
        run_broadfactor_ingestao,
        dry_run=False,
        limit=limit,
    )
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "dry_run": False,
            "limit": limit,
        },
    )
