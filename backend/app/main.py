from datetime import datetime, timedelta, timezone

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import admin, alcadas, auth, components, escaladas, operations, overrides, pricing, uploads
from app.api.v1.endpoints.uploads import public_router as uploads_public_router
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.database import supabase

logger = structlog.get_logger()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"]
    if settings.DEBUG
    else [
        settings.FRONTEND_URL,
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://credit-engine-silk.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
_auth_dep = [Depends(get_current_user)]

app.include_router(operations.router, prefix="/api/v1/operations", tags=["operations"], dependencies=_auth_dep)
app.include_router(components.router, prefix="/api/v1/components", tags=["components"], dependencies=_auth_dep)
app.include_router(uploads.router,    prefix="/api/v1/uploads",    tags=["uploads"],    dependencies=_auth_dep)
app.include_router(admin.router,      prefix="/api/v1/admin",      tags=["admin"],      dependencies=_auth_dep)
app.include_router(overrides.router,  prefix="/api/v1/overrides",  tags=["overrides"],  dependencies=_auth_dep)
app.include_router(auth.router,       prefix="/api/v1/auth",       tags=["auth"])
app.include_router(alcadas.router,    prefix="/api/v1/alcadas",    tags=["alcadas"],    dependencies=_auth_dep)
app.include_router(escaladas.router,  prefix="/api/v1/escaladas",  tags=["escaladas"],  dependencies=_auth_dep)
app.include_router(pricing.router,    prefix="/api/v1/pricing",    tags=["pricing"],    dependencies=_auth_dep)
app.include_router(operations.router, prefix="/api/operations",    tags=["operations"], dependencies=_auth_dep)
app.include_router(escaladas.router,  prefix="/api/escaladas",     tags=["escaladas"],  dependencies=_auth_dep)

# Rotas p?blicas por design (link de upload enviado ao fornecedor ? sem auth)
app.include_router(uploads_public_router, prefix="/api/v1/uploads", tags=["uploads-public"])


async def _recover_stale_operations():
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

        # Opera??es sem heartbeat nos ?ltimos 10 min (ou nunca tiveram heartbeat e t?m > 10min)
        result = supabase.table("operations")\
            .select("id,status")\
            .in_("status", ["pending", "processing"])\
            .or_(
                f"heartbeat_at.is.null,heartbeat_at.lt.{cutoff}"
            )\
            .lt("created_at", cutoff)\
            .execute()

        total = 0
        for operation in result.data or []:
            operation_id = operation.get("id")
            status_anterior = operation.get("status")
            if not operation_id:
                continue
            try:
                supabase.table("operations")\
                    .update({
                        "status": "failed",
                        "error_message": "operacao interrompida por restart do container",
                    })\
                    .eq("id", operation_id)\
                    .execute()
                total += 1
                logger.warning(
                    "startup.operation_recovered",
                    operation_id=operation_id,
                    status_anterior=status_anterior,
                )
            except Exception as exc:
                logger.error(
                    "startup.operation_recovery_update_failed",
                    operation_id=operation_id,
                    status_anterior=status_anterior,
                    error=str(exc),
                )

        logger.info("startup.recovery_complete", total=total)
    except Exception as exc:
        logger.error("startup.recovery_failed", error=str(exc))


@app.on_event("startup")
async def startup_recovery():
    await _recover_stale_operations()


@app.get("/health")
def health():
    return {"status": "ok", "version": settings.APP_VERSION}
