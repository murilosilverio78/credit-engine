import asyncio

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import admin, alcadas, auth, components, eligibility, escaladas, internal, operations, overrides, pricing, uploads
from app.api.v1.endpoints.uploads import public_router as uploads_public_router
from app.core.auth import get_current_user
from app.core.config import settings
from app.services.operation_watchdog_service import run_operation_watchdog

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
app.include_router(eligibility.router, prefix="/api/v1/elegibilidade", tags=["eligibility"], dependencies=_auth_dep)

# Rota de maquina com autenticacao propria via X-Internal-Token.
app.include_router(internal.router, prefix="/api/v1/internal", tags=["internal"])

# Rotas públicas por design (link de upload enviado ao fornecedor — sem auth)
app.include_router(uploads_public_router, prefix="/api/v1/uploads", tags=["uploads-public"])


async def _recover_stale_operations():
    try:
        summary = await asyncio.to_thread(run_operation_watchdog)
        logger.info("startup.recovery_complete", **summary)
    except Exception as exc:
        logger.error("startup.recovery_failed", error=str(exc))


@app.on_event("startup")
async def startup_recovery():
    await _recover_stale_operations()


@app.get("/health")
def health():
    return {"status": "ok", "version": settings.APP_VERSION}
