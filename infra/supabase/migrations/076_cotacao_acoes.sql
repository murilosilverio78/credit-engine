-- Fatia 3: ações manuais da tela de cotação.
-- Esta migration é aplicada pelo operador de infraestrutura, nunca pelo backend.
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'quote_reframed';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'quote_free_checks_requested';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'quote_full_analysis_requested';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'quote_archived';
