-- Migration 017: penalties for missing manual certificates

INSERT INTO pricing_parameters (key, value, label, unit, grupo) VALUES
  (
    'penalidade_cnd_federal_ausente',
    6,
    'Penalidade por CND Federal ausente',
    'pontos',
    'risco_credito'
  ),
  (
    'penalidade_cndt_ausente',
    6,
    'Penalidade por CNDT ausente',
    'pontos',
    'risco_credito'
  ),
  (
    'penalidade_fgts_ausente',
    6,
    'Penalidade por certificado FGTS ausente',
    'pontos',
    'risco_credito'
  )
ON CONFLICT (key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
