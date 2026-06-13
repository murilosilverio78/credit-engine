-- TTL de cache configuravel por componente (horas); NULL = usar default do codigo
ALTER TABLE component_config ADD COLUMN IF NOT EXISTS cache_ttl_hours INTEGER;

-- Revogacao de sessao: incrementar invalida todos os JWTs anteriores do usuario
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
