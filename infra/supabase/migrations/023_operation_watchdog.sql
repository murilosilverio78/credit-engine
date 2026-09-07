-- Operation watchdog configuration.

INSERT INTO eligibility_parameters (key, value, label, unit, grupo) VALUES
  (
    'watchdog_heartbeat_timeout_minutos',
    15,
    'Timeout de heartbeat do watchdog',
    'minutos',
    'operacional'
  )
ON CONFLICT (key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
