-- =============================================================
-- Migration 008: parâmetros de precificação (substitui hard-code)
-- =============================================================

-- 1. Parâmetros escalares (chave-valor)
CREATE TABLE IF NOT EXISTS pricing_parameters (
  key         VARCHAR(60) PRIMARY KEY,
  value       NUMERIC(12,6) NOT NULL,
  label       TEXT NOT NULL,
  unit        VARCHAR(20) NOT NULL,        -- '% a.m.' | '% a.a.' | '% PL' | 'flat' | '% rec' | '% ops' | '% perda'
  grupo       VARCHAR(40) NOT NULL,        -- 'estrutura_capital' | 'custos_operacionais' | 'risco_credito'
  updated_by  UUID,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE pricing_parameters ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all" ON pricing_parameters
  FOR ALL TO service_role USING (TRUE);

-- 2. Matriz de rating (uma linha por rating; E = recusa)
CREATE TABLE IF NOT EXISTS pricing_rating_matrix (
  rating          CHAR(1) PRIMARY KEY,     -- A, B, C, D, E
  pd_mult         NUMERIC(8,4) NOT NULL,   -- % PD Obs (ex: 0.60 = 60%)
  lgd_mult        NUMERIC(8,4) NOT NULL,   -- % LGD Obs (ex: 0.70 = 70%)
  bond_cobertura  NUMERIC(8,4) NOT NULL,   -- cobertura bond (ex: 0.50 = 50%)
  bond_premio_aa  NUMERIC(8,4),            -- prêmio bond a.a.; NULL quando recusa
  recusa          BOOLEAN NOT NULL DEFAULT FALSE,
  perfil          TEXT,                    -- descrição do perfil cedente/operação
  ordem           INTEGER NOT NULL,        -- para ordenação na tela (1=A ... 5=E)
  updated_by      UUID,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE pricing_rating_matrix ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all" ON pricing_rating_matrix
  FOR ALL TO service_role USING (TRUE);

-- 3. Seed dos parâmetros escalares (valores atuais da planilha)
INSERT INTO pricing_parameters (key, value, label, unit, grupo) VALUES
  ('cdi_am',                0.0114, 'CDI atual',                          '% a.m.', 'estrutura_capital'),
  ('pct_subordinado',       0.20,   '% Subordinação (Júnior / PL total)', '% PL',   'estrutura_capital'),
  ('pct_mezzanino',         0.20,   '% Mezzanino (Mezzanino / PL total)', '% PL',   'estrutura_capital'),
  ('spread_mez_aa',         0.10,   'Spread Debênture Mezzanino (a.a.)',  '% a.a.', 'estrutura_capital'),
  ('spread_senior_aa',      0.05,   'Spread Debênture Sênior (a.a.)',     '% a.a.', 'estrutura_capital'),
  ('margem_alvo_sub_am',    0.025,  'Margem-alvo Subordinado (a.m.)',     '% a.m.', 'estrutura_capital'),
  ('taxa_adm_aa',           0.01,   'Taxa Administração',                 '% a.a.', 'custos_operacionais'),
  ('bancarizacao',          0.035,  'Bancarização',                       'flat',   'custos_operacionais'),
  ('serpro_ate_300k',       0.0042, 'Fee SERPRO — Faixa ≤ R$ 300k',       'flat',   'custos_operacionais'),
  ('serpro_300k_1m',        0.0034, 'Fee SERPRO — Faixa R$ 300k–1M',      'flat',   'custos_operacionais'),
  ('serpro_1m_10m',         0.0027, 'Fee SERPRO — Faixa R$ 1M–10M',       'flat',   'custos_operacionais'),
  ('serpro_acima_10m',      0.0022, 'Fee SERPRO — Faixa > R$ 10M',        'flat',   'custos_operacionais'),
  ('custo_orig_analise',    0.005,  'Custo Plataforma Originação/Análise','flat',   'custos_operacionais'),
  ('custo_plataforma_perf', 0.005,  'Custo Plataforma Performance',       '% rec',  'custos_operacionais'),
  ('pd_performada',         0.016,  'PD observada (ops performadas)',     '% ops',  'risco_credito'),
  ('lgd_estimada',          0.70,   'LGD estimada',                       '% perda','risco_credito')
ON CONFLICT (key) DO NOTHING;

-- 4. Seed da matriz de rating (valores atuais da planilha)
INSERT INTO pricing_rating_matrix
  (rating, pd_mult, lgd_mult, bond_cobertura, bond_premio_aa, recusa, perfil, ordem) VALUES
  ('A', 0.60, 0.70, 0.50, 0.010, FALSE, 'Cedente ≥2 anos + histórico positivo portal + contrato >18m + margem ≤50%', 1),
  ('B', 1.00, 0.80, 0.70, 0.020, FALSE, 'Cedente ≥1 ano + sem restrições + contrato >12m + margem ≤60%',           2),
  ('C', 2.00, 0.95, 0.85, 0.035, FALSE, 'Cedente sem histórico portal + contrato 6–12m + margem ≤65%',             3),
  ('D', 5.00, 1.10, 1.00, 0.050, FALSE, 'Cedente novo ou ocorrência + contrato <6m + margem próxima 70%',          4),
  ('E', 10.00, 1.30, 1.00, NULL, TRUE,  'Risco elevado — RECUSA. Exige garantia adicional',                        5)
ON CONFLICT (rating) DO NOTHING;

-- Após rodar, commit:
-- git add infra/supabase/migrations/008_pricing_parameters.sql
-- git commit -m "migration: pricing_parameters and pricing_rating_matrix tables with seed"
