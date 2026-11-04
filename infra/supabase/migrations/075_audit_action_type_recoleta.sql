ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'operation_recollection_requested';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'operation_recollection_completed';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'component_refresh_failed';
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'manual_operation_quote_linked';
NOTIFY pgrst, 'reload schema';
