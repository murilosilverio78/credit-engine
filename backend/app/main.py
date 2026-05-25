from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.endpoints import operations, components, uploads, admin

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.DEBUG else [settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(operations.router, prefix="/api/v1/operations", tags=["operations"])
app.include_router(components.router, prefix="/api/v1/components", tags=["components"])
app.include_router(uploads.router,    prefix="/api/v1/uploads",    tags=["uploads"])
app.include_router(admin.router,      prefix="/api/v1/admin",      tags=["admin"])


@app.get("/health")
def health():
    return {"status": "ok", "version": settings.APP_VERSION}
