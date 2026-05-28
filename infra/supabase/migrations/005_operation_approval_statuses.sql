ALTER TYPE operation_status ADD VALUE IF NOT EXISTS 'approved';
ALTER TYPE operation_status ADD VALUE IF NOT EXISTS 'rejected';
ALTER TYPE operation_status ADD VALUE IF NOT EXISTS 'escalated';
