-- Penalidade no score final por ausencia de balanco ou DRE do ultimo exercicio.

INSERT INTO pricing_parameters (key, value, label, unit, grupo) VALUES
  (
    'penalidade_balanco_ausente',
    10,
    'Penalidade por balanco ou DRE ausente',
    'pontos',
    'risco_credito'
  )
ON CONFLICT (key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
