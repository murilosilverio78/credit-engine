from typing import Optional

from fastapi import Depends, HTTPException, Request
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
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
    except JWTError:
        return None

    user_id = payload.get("id") or payload.get("sub") or payload.get("user_id")
    if user_id:
        payload["id"] = user_id
    if payload.get("role") and not payload.get("alcada"):
        payload["alcada"] = ROLE_TO_ALCADA.get(payload["role"], "analyst")
    return payload


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
    raise HTTPException(status_code=401, detail="Não autenticado")


CurrentUser = Depends(get_current_user)
