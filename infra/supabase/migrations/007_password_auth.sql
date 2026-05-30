-- =============================================================
-- Migration 007: password auth + email verification
-- Substitui magic_link por login/senha com confirmação de email
-- =============================================================

-- 1. Adicionar password_hash e email_verified à tabela users
--    (tabela users existe mas não está em migrations — criada manualmente)
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS password_hash TEXT,
  ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. Tabela de tokens de confirmação de email
CREATE TABLE IF NOT EXISTS email_verification_tokens (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token       VARCHAR(100) UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(32), 'hex'),
  used        BOOLEAN NOT NULL DEFAULT FALSE,
  expires_at  TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evt_token     ON email_verification_tokens(token);
CREATE INDEX IF NOT EXISTS idx_evt_user_id   ON email_verification_tokens(user_id);

-- RLS
ALTER TABLE email_verification_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all" ON email_verification_tokens
  FOR ALL TO service_role USING (TRUE);

-- 3. Tabela de tokens de reset de senha (para uso futuro)
CREATE TABLE IF NOT EXISTS password_reset_tokens (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token       VARCHAR(100) UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(32), 'hex'),
  used        BOOLEAN NOT NULL DEFAULT FALSE,
  expires_at  TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '1 hour'),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prt_token ON password_reset_tokens(token);
ALTER TABLE password_reset_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all" ON password_reset_tokens
  FOR ALL TO service_role USING (TRUE);

-- Após rodar, fazer commit do arquivo SQL:
-- git add infra/supabase/migrations/007_password_auth.sql
-- git commit -m "migration: add password_hash, email_verified and verification tokens"
