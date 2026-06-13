"""StorageService: upload e download de documentos no Cloudflare R2."""
import asyncio
from functools import lru_cache

import boto3
from botocore.config import Config
import structlog

from app.core.config import settings


logger = structlog.get_logger()


class R2UnavailableError(RuntimeError):
    """Raised when R2 is not configured for document storage."""


def _is_placeholder(value: str) -> bool:
    normalized = value.lower()
    return not value or any(marker in normalized for marker in ("seu-", "your-", "xxxx"))


def is_r2_configured() -> bool:
    return all(
        not _is_placeholder(value)
        for value in (
            settings.R2_ACCOUNT_ID,
            settings.R2_ACCESS_KEY_ID,
            settings.R2_SECRET_ACCESS_KEY,
            settings.R2_BUCKET_NAME,
        )
    )


@lru_cache()
def get_r2_client():
    if not is_r2_configured():
        raise R2UnavailableError("Cloudflare R2 is not configured")

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


class StorageService:
    MIME_EXTENSIONS = {
        "application/pdf": "pdf",
        "image/jpeg": "jpg",
        "image/png": "png",
    }

    async def upload_document(
        self,
        operation_id: str,
        document_type: str,
        content: bytes,
        filename: str,
        mime_type: str,
    ) -> str:
        """
        Faz upload do documento para o R2.
        Retorna a storage_key do objeto.
        """
        import uuid

        ext = self.MIME_EXTENSIONS.get(mime_type, "bin")
        storage_key = f"operations/{operation_id}/{document_type}/{uuid.uuid4()}.{ext}"

        client = get_r2_client()
        await asyncio.to_thread(
            client.put_object,
            Bucket=settings.R2_BUCKET_NAME,
            Key=storage_key,
            Body=content,
            ContentType=mime_type,
        )

        logger.info(
            "storage.uploaded",
            operation_id=operation_id,
            storage_key=storage_key,
            size=len(content),
        )
        return storage_key

    async def get_presigned_url(self, storage_key: str, expires_in: int = 3600) -> str:
        """Gera URL pré-assinada para download seguro (expira em 1h por padrão)."""
        client = get_r2_client()
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.R2_BUCKET_NAME, "Key": storage_key},
            ExpiresIn=expires_in,
        )
        return url

    async def delete(self, storage_key: str):
        """Remove documento do R2."""
        client = get_r2_client()
        await asyncio.to_thread(
            client.delete_object,
            Bucket=settings.R2_BUCKET_NAME,
            Key=storage_key,
        )
        logger.info("storage.deleted", storage_key=storage_key)
