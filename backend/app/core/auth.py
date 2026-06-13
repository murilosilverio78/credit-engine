from typing import Optional

from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt

from app.core.config import settings
from app.core.database import supabase


ROLE_TO_ALCADA = {
    "analista": "analista",
    "gerente": "gerente",
    "diretor": "diretor",
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
        payload["alcada"] = ROLE_TO_ALCADA.get(payload["role"], "analista")
    if "token_version" not in payload:
        payload["token_version"] = 0
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
    user = _decode_token(token)
    if not user:
        return None
    if not await _validate_against_db(user):
        return None
    return user


async def _validate_against_db(user: dict) -> bool:
    if not user.get("id"):
        return False
    # Débito técnico: +1 query por request; aceitável no volume atual.
    # Se virar gargalo, otimizar com cache curto por usuário/token_version.
    try:
        result = supabase.table("users")\
            .select("token_version,active")\
            .eq("id", user["id"])\
            .single()\
            .execute()
        row = result.data
    except Exception:
        row = None

    if not row or row.get("active") is False:
        return False

    if (row.get("token_version") or 0) != (user.get("token_version") or 0):
        return False

    return True


async def get_current_user(request: Request) -> dict:
    user = await get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return user


CurrentUser = Depends(get_current_user)
