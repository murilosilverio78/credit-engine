from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_NAME: str = "AntecipaGov Credit Engine"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    SECRET_KEY: str
    NEXTAUTH_SECRET: str = ""

    # Supabase
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str          # service role key (backend only)
    DATABASE_URL: str                  # postgresql+asyncpg://...

    # Redis (Upstash)
    REDIS_URL: str                     # rediss://...

    # Cloudflare R2
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = ""
    R2_PUBLIC_URL: str = ""

    # Claude API
    ANTHROPIC_API_KEY: str

    # Portal da Transparência
    PORTAL_TRANSPARENCIA_TOKEN: str

    # 2captcha
    TWOCAPTCHA_API_KEY: str

    # Email
    SMTP_HOST: str
    SMTP_PORT: int = 587
    SMTP_USER: str
    SMTP_PASS: str
    EMAIL_FROM: str = "credito@antecipagov.com.br"

    # Celery
    CELERY_BROKER_URL: str             # mesmo REDIS_URL
    CELERY_RESULT_BACKEND: str         # mesmo REDIS_URL

    # Upload task SLA
    UPLOAD_TASK_EXPIRY_HOURS: int = 48
    OPERATION_EXPIRY_HOURS: int = 48

    # Frontend URL (para links em emails)
    FRONTEND_URL: str = "http://localhost:3000"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
