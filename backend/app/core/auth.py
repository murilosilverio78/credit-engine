from typing import Optional

from fastapi import Depends, Request
from jose import JWTError, jwt

from app.core.config import settings


ROLE_TO_ALCADA = {
    "analista": "analyst",
    "gerente": "manager",
    "diretor": "committee",
}


def _secret() -> str:
    return settings.NEXTAUTH_SECRET or settings.SECRET_KEY


def _decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"])
    except JWTError:
        return None


async def get_current_user_optional(request: Request) -> Optional[dict]:
    auth = request.headers.get("authorization", "")
    token = None
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get("session")
    if not token:
        return None
    return _decode_token(token)


async def get_current_user(request: Request) -> dict:
    user = await get_current_user_optional(request)
    if user:
        return user

    # Compatibility fallback while the Admin UI uses cross-origin FastAPI calls.
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "system@credit-engine.local",
        "name": "Sistema",
        "role": "diretor",
        "alcada": "committee",
    }


CurrentUser = Depends(get_current_user)
