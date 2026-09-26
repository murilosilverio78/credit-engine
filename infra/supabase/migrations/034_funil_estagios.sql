-- Migration 034: funil de triagem Broadfactor em quatro estagios

ALTER TYPE operation_status ADD VALUE IF NOT EXISTS 'aguardando_relatorio';

ALTER TABLE cotacoes_broadfactor
  ADD COLUMN IF NOT EXISTS ambiente VARCHAR(20) NOT NULL DEFAULT 'PRODUCAO',
  ADD COLUMN IF NOT EXISTS estagio VARCHAR(20) NOT NULL DEFAULT 'LISTA_ESPERA',
  ADD COLUMN IF NOT EXISTS estagio_max VARCHAR(20),
  ADD COLUMN IF NOT EXISTS estagio_motivo TEXT,
  ADD COLUMN IF NOT EXISTS estagio_atualizado_em TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS n_documentos INTEGER,
  ADD COLUMN IF NOT EXISTS tipos_documento TEXT[];

DO $$ BEGIN
  ALTER TABLE cotacoes_broadfactor
    ADD CONSTRAINT cotacoes_broadfactor_estagio_check
    CHECK (estagio IN (
      'LISTA_ESPERA',
      'ENQUADRADA',
      'DOCUMENTADA',
      'QUALIFICADA',
      'ENCERRADA'
    ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE cotacoes_broadfactor
    ADD CONSTRAINT cotacoes_broadfactor_estagio_max_check
    CHECK (
      estagio_max IS NULL OR estagio_max IN (
        'LISTA_ESPERA',
        'ENQUADRADA',
        'DOCUMENTADA',
        'QUALIFICADA'
      )
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_cotacoes_broadfactor_ambiente_estagio
  ON cotacoes_broadfactor(ambiente, estagio);

INSERT INTO eligibility_parameters (key, value, label, unit, grupo)
VALUES
  ('funil_hist_min_meses', 6, 'Historico minimo para qualificar no funil', 'meses', 'funil'),
  ('funil_orgaos_min', 2, 'Orgaos pagadores minimos para qualificar no funil', 'quantidade', 'funil'),
  ('funil_cobertura_min', 2.0, 'Cobertura minima recebido acumulado / valor enquadrado', 'decimal', 'funil')
ON CONFLICT (key) DO NOTHING;

UPDATE cotacoes_broadfactor
SET
  estagio = CASE WHEN operation_id IS NOT NULL THEN 'QUALIFICADA' ELSE 'LISTA_ESPERA' END,
  estagio_max = CASE WHEN operation_id IS NOT NULL THEN 'QUALIFICADA' ELSE 'LISTA_ESPERA' END,
  estagio_atualizado_em = COALESCE(estagio_atualizado_em, NOW())
WHERE ambiente = 'PRODUCAO'
  AND estagio = 'LISTA_ESPERA';

NOTIFY pgrst, 'reload schema';
