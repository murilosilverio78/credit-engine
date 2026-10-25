-- Capacidade de contratos sob demanda pela execução comprovada no Comprasnet.
-- Entregue para aplicação controlada; não aplicar automaticamente.

INSERT INTO eligibility_parameters (key, value, unit, label, grupo) VALUES
  ('cap_sob_demanda_janela_meses', 6, 'meses', 'Janela de execução comprovada para contratos sob demanda', 'capacidade'),
  ('cap_sob_demanda_min_faturas', 2, 'faturas', 'Mínimo de faturas na janela para comprovar execução sob demanda', 'capacidade')
ON CONFLICT (key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
