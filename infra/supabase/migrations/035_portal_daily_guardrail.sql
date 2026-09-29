-- Persistent daily Portal usage. The row lock makes each reservation atomic.
CREATE TABLE IF NOT EXISTS portal_daily_usage (
  usage_date DATE PRIMARY KEY,
  request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
  alert_70_sent BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE portal_daily_usage ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION claim_portal_daily_request(p_limit INTEGER)
RETURNS TABLE (
  allowed BOOLEAN,
  consumed INTEGER,
  alert_now BOOLEAN,
  usage_date DATE
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_date DATE := (NOW() AT TIME ZONE 'America/Sao_Paulo')::DATE;
  v_count INTEGER;
  v_alerted BOOLEAN;
  v_next INTEGER;
  v_alert_now BOOLEAN;
BEGIN
  INSERT INTO portal_daily_usage (usage_date)
  VALUES (v_date)
  ON CONFLICT ON CONSTRAINT portal_daily_usage_pkey DO NOTHING;

  SELECT request_count, alert_70_sent
    INTO v_count, v_alerted
    FROM portal_daily_usage
   WHERE portal_daily_usage.usage_date = v_date
   FOR UPDATE;

  IF v_count >= GREATEST(p_limit, 0) THEN
    RETURN QUERY SELECT FALSE, v_count, FALSE, v_date;
    RETURN;
  END IF;

  v_next := v_count + 1;
  v_alert_now := NOT v_alerted
    AND v_next >= CEIL(GREATEST(p_limit, 0) * 0.7)::INTEGER;

  UPDATE portal_daily_usage
     SET request_count = v_next,
         alert_70_sent = v_alerted OR v_alert_now,
         updated_at = NOW()
   WHERE portal_daily_usage.usage_date = v_date;

  RETURN QUERY SELECT TRUE, v_next, v_alert_now, v_date;
END;
$$;

CREATE OR REPLACE FUNCTION get_portal_daily_usage()
RETURNS TABLE (
  consumed INTEGER,
  alert_70_sent BOOLEAN,
  usage_date DATE
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT
    COALESCE(p.request_count, 0)::INTEGER,
    COALESCE(p.alert_70_sent, FALSE),
    (NOW() AT TIME ZONE 'America/Sao_Paulo')::DATE
  FROM (SELECT 1) AS seed
  LEFT JOIN portal_daily_usage AS p
    ON p.usage_date = (NOW() AT TIME ZONE 'America/Sao_Paulo')::DATE;
$$;

REVOKE ALL ON FUNCTION claim_portal_daily_request(INTEGER)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION get_portal_daily_usage()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_portal_daily_request(INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION get_portal_daily_usage() TO service_role;
