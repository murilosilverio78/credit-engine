"""
Conexão centralizada com Supabase.
Usa supabase-py com service role key (acesso total, backend only).
"""
from supabase import create_client, Client
from functools import lru_cache
from app.core.config import settings


@lru_cache()
def get_supabase() -> Client:
    return create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_KEY,
    )


# Instância global reutilizável
supabase: Client = get_supabase()
