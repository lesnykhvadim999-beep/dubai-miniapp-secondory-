-- PHASE BO O2 — Unified observability: SQL view v_system_health
-- Failure-tolerant: each sub-query falls back to 0 if its table is missing.
-- Apply to: intelligence DB.
-- Vadim Realty + RERA BRN 65011.

CREATE OR REPLACE FUNCTION _sysh_safe_count(_sql text) RETURNS bigint AS $$
DECLARE
  v bigint;
BEGIN
  EXECUTE _sql INTO v;
  RETURN COALESCE(v, 0);
EXCEPTION WHEN OTHERS THEN
  RETURN 0;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE VIEW v_system_health AS
SELECT
  _sysh_safe_count(
    $q$SELECT COUNT(*) FROM bot_heartbeats
       WHERE last_beat_at > NOW() - interval '5 min'$q$
  )::int AS bots_alive_5min,
  _sysh_safe_count(
    $q$SELECT COUNT(*) FROM bot_heartbeats$q$
  )::int AS bots_total,
  _sysh_safe_count(
    $q$SELECT COUNT(*) FROM circuit_breakers WHERE state='OPEN'$q$
  )::int AS providers_down,
  _sysh_safe_count(
    $q$SELECT COUNT(*) FROM feature_flags WHERE auto_disabled_at IS NOT NULL$q$
  )::int AS features_disabled,
  _sysh_safe_count(
    $q$SELECT COUNT(*) FROM feature_flags
       WHERE auto_disabled_at IS NULL AND enabled=true$q$
  )::int AS features_enabled,
  _sysh_safe_count(
    $q$SELECT COUNT(*) FROM audit_findings
       WHERE severity IN ('HIGH','CRITICAL') AND verified_resolved=false$q$
  )::int AS critical_findings,
  _sysh_safe_count(
    $q$SELECT COUNT(*) FROM immune_incidents
       WHERE status='pending' AND last_seen > NOW() - interval '24 hours'$q$
  )::int AS pending_incidents,
  _sysh_safe_count(
    $q$SELECT COUNT(DISTINCT user_id) FROM bot_interactions
       WHERE occurred_at > NOW() - interval '24 hours'$q$
  )::int AS active_users_24h,
  NOW() AS computed_at;

COMMENT ON VIEW v_system_health IS
  'PHASE BO O2 unified observability snapshot. Failure-tolerant: missing tables -> 0.';
