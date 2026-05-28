from datetime import datetime, timedelta, timezone
from uuid import uuid4

import aiosmtplib
from email.message import EmailMessage
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import jwt
from pydantic import BaseModel, EmailStr

from app.core.auth import ROLE_TO_ALCADA, get_current_user
from app.core.config import settings
from app.core.database import supabase


router = APIRouter()


class MagicLinkInput(BaseModel):
    email: EmailStr


def _secret() -> str:
    return settings.NEXTAUTH_SECRET or settings.SECRET_KEY


async def _send_email(to_email: str, link: str):
    message = EmailMessage()
    message["From"] = settings.EMAIL_FROM
    message["To"] = to_email
    message["Subject"] = "Seu link de acesso ao Credit Engine"
    message.set_content(f"Acesse o Credit Engine por este link: {link}")

    await aiosmtplib.send(
        message,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASS,
        start_tls=settings.SMTP_PORT != 465,
    )


@router.post("/magic-link")
async def create_magic_link(payload: MagicLinkInput):
    result = supabase.table("users")\
        .select("id,email,name,role,active")\
        .eq("email", payload.email.lower())\
        .single()\
        .execute()
    user = result.data
    if not user or not user.get("active"):
        raise HTTPException(status_code=403, detail="Usuário não autorizado")

    token = str(uuid4())
    supabase.table("magic_link_tokens").insert({
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        "token": token,
        "user_id": user["id"],
        "used": False,
    }).execute()

    await _send_email(
        user["email"],
        f"{settings.FRONTEND_URL}/api/auth/verify?token={token}",
    )
    return {"ok": True}


@router.get("/verify")
async def verify_magic_link(token: str, response: Response):
    token_result = supabase.table("magic_link_tokens")\
        .select("token,user_id,expires_at,used")\
        .eq("token", token)\
        .eq("used", False)\
        .single()\
        .execute()
    magic = token_result.data
    if not magic or datetime.fromisoformat(magic["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Token inválido ou expirado")

    user_result = supabase.table("users")\
        .select("id,email,name,role,active")\
        .eq("id", magic["user_id"])\
        .single()\
        .execute()
    user = user_result.data
    if not user or not user.get("active"):
        raise HTTPException(status_code=403, detail="Usuário não autorizado")

    session_token = str(uuid4())
    supabase.table("magic_link_tokens").update({"used": True}).eq("token", token).execute()
    supabase.table("user_sessions").insert({
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "session_token": session_token,
        "user_id": user["id"],
    }).execute()

    encoded = jwt.encode(
        {
            "alcada": ROLE_TO_ALCADA.get(user["role"], "analyst"),
            "email": user["email"],
            "exp": datetime.now(timezone.utc) + timedelta(days=7),
            "id": user["id"],
            "name": user.get("name") or user["email"],
            "role": user["role"],
            "session_token": session_token,
        },
        _secret(),
        algorithm="HS256",
    )
    response.set_cookie("session", encoded, httponly=True, samesite="lax", secure=True)
    return {"ok": True}


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}
