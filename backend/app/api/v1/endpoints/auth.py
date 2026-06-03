from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Literal
from uuid import uuid4

import aiosmtplib
import bcrypt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from jose import jwt
from pydantic import BaseModel, EmailStr

from app.core.auth import ROLE_TO_ALCADA, get_current_user
from app.core.config import settings
from app.core.database import supabase


router = APIRouter()
logger = structlog.get_logger()


class RegisterInput(BaseModel):
    email: EmailStr
    name: str
    role: Literal["analista", "gerente", "diretor"]
    password: str


class LoginInput(BaseModel):
    email: EmailStr
    password: str


class ConfirmEmailInput(BaseModel):
    token: str


class ResendInput(BaseModel):
    email: EmailStr


def _secret() -> str:
    return settings.NEXTAUTH_SECRET or settings.SECRET_KEY


async def _send_email(
    to_email: str,
    link: str,
    subject: str = "Acesso ao Credit Engine",
    body: str | None = None,
):
    message = EmailMessage()
    message["From"] = settings.EMAIL_FROM
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body or f"Acesse: {link}")

    await aiosmtplib.send(
        message,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASS,
        start_tls=settings.SMTP_PORT != 465,
    )


async def _send_email_safe(
    to_email: str,
    link: str,
    subject: str = "Acesso ao Credit Engine",
    body: str | None = None,
) -> bool:
    """Tenta enviar email; loga falha sem propagar. Retorna True se enviou."""
    try:
        await _send_email(to_email, link, subject=subject, body=body)
        return True
    except Exception as exc:
        logger.error("email.send_failed", to=to_email, error=str(exc))
        return False


@router.post("/register", status_code=201)
async def register_user(
    payload: RegisterInput,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "diretor":
        raise HTTPException(status_code=403, detail="Acesso negado")

    existing = supabase.table("users")\
        .select("id")\
        .eq("email", payload.email.lower())\
        .execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="Email já cadastrado")

    hashed = bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode()

    result = supabase.table("users").insert({
        "email": payload.email.lower(),
        "name": payload.name,
        "role": payload.role,
        "active": True,
        "password_hash": hashed,
        "email_verified": False,
    }).execute()
    user = result.data[0]

    token_result = supabase.table("email_verification_tokens").insert({
        "user_id": user["id"],
    }).execute()
    token = token_result.data[0]["token"]

    verify_link = f"{settings.FRONTEND_URL}/auth/confirm-email?token={token}"
    email_sent = await _send_email_safe(
        user["email"],
        verify_link,
        subject="Confirme seu email — Credit Engine AntecipaGov",
        body=(
            f"Olá {payload.name},\n\n"
            f"Seu acesso ao Credit Engine foi criado.\n"
            f"Confirme seu email clicando no link abaixo (válido por 24h):\n\n"
            f"{verify_link}\n\n"
            f"Após confirmar, use seu email e a senha definida pelo administrador para entrar."
        ),
    )
    return {
        "ok": True,
        "user_id": user["id"],
        "email": user["email"],
        "email_sent": email_sent,
    }


@router.post("/login")
async def login(payload: LoginInput):
    erro_credencial = "Email ou senha inválidos"

    try:
        result = supabase.table("users")\
            .select("id,email,name,role,active,password_hash,email_verified")\
            .eq("email", payload.email.lower())\
            .single()\
            .execute()
        user = result.data
    except Exception:
        user = None

    if not user or not user.get("active"):
        raise HTTPException(status_code=401, detail=erro_credencial)

    if not user.get("password_hash"):
        raise HTTPException(status_code=401, detail=erro_credencial)

    if not bcrypt.checkpw(payload.password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail=erro_credencial)

    if not user.get("email_verified"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "EMAIL_NOT_VERIFIED",
                "message": (
                    "Confirme seu email antes de fazer login. "
                    "Verifique sua caixa de entrada."
                ),
            },
        )

    session_token = str(uuid4())
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
    return {
        "access_token": encoded,
        "ok": True,
        "token_type": "bearer",
    }


@router.post("/confirm-email")
async def confirm_email(payload: ConfirmEmailInput):
    try:
        result = supabase.table("email_verification_tokens")\
            .select("id,user_id,used,expires_at")\
            .eq("token", payload.token)\
            .eq("used", False)\
            .single()\
            .execute()
        row = result.data
    except Exception:
        row = None

    if not row:
        raise HTTPException(status_code=400, detail="Token inválido ou já utilizado")

    if datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Token expirado")

    supabase.table("email_verification_tokens")\
        .update({"used": True})\
        .eq("id", row["id"])\
        .execute()
    supabase.table("users")\
        .update({"email_verified": True})\
        .eq("id", row["user_id"])\
        .execute()

    return {"ok": True, "message": "Email confirmado. Você já pode fazer login."}


@router.post("/resend-verification")
async def resend_verification(payload: ResendInput):
    msg = "Se o email existir e ainda não estiver confirmado, um novo link foi enviado."

    try:
        result = supabase.table("users")\
            .select("id,email,name,email_verified,active")\
            .eq("email", payload.email.lower())\
            .single()\
            .execute()
        user = result.data
    except Exception:
        user = None

    if not user or not user.get("active") or user.get("email_verified"):
        return {"ok": True, "message": msg}

    token_result = supabase.table("email_verification_tokens").insert({
        "user_id": user["id"],
    }).execute()
    token = token_result.data[0]["token"]
    verify_link = f"{settings.FRONTEND_URL}/auth/confirm-email?token={token}"

    await _send_email_safe(
        user["email"],
        verify_link,
        subject="Novo link de confirmação — Credit Engine",
        body=f"Acesse o link para confirmar seu email (válido por 24h):\n\n{verify_link}",
    )
    return {"ok": True, "message": msg}


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("session", samesite="none", secure=True)
    return {"ok": True}
