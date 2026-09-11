"""
Conexão centralizada com Supabase.
Usa supabase-py com service role key (acesso total, backend only).
"""
from functools import lru_cache

import httpx
from supabase import Client, create_client

from app.core.config import settings


def _replace_postgrest_session_with_http1(client: Client) -> Client:
    """Replace postgrest 0.16's hardcoded HTTP/2 session with HTTP/1.1."""
    current_session = client.postgrest.session
    client.postgrest.session = httpx.Client(
        base_url=current_session.base_url,
        headers=current_session.headers,
        timeout=current_session.timeout,
        follow_redirects=current_session.follow_redirects,
        http2=False,
    )
    current_session.close()
    return client


@lru_cache()
def get_supabase() -> Client:
    client = create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_KEY,
    )
    return _replace_postgrest_session_with_http1(client)


# Instância global reutilizável
supabase: Client = get_supabase()
