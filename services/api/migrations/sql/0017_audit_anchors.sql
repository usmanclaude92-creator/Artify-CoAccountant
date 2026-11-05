-- Phase 12 (continued): audit anchors and the platform audit chain.
--  * app.audit_heads() lets the operations job read every chain's newest seal so it can be copied OUTSIDE the database;
--    app.audit_seal_at(org, seq) answers "what is the hash of seal <seq>?" so a copied head can be checked later. If the newest
--    seals were cut off, or history was rewritten, a previously exported head no longer matches.
--  * the platform audit log (platform_audit_events) gets its own single chain, sealed by the same job.

GRANT SELECT ON platform_audit_events TO artify_definer;
CREATE POLICY definer_select_platform_audit ON platform_audit_events FOR SELECT TO artify_definer USING (true);

CREATE TABLE platform_audit_seals (
  seq              bigint PRIMARY KEY CHECK (seq >= 1),
  audit_event_id   uuid NOT NULL UNIQUE,
  audit_created_at timestamptz NOT NULL,
  prev_hash        bytea,
  row_hash         bytea NOT NULL,
  sealed_at        timestamptz NOT NULL DEFAULT now(),
  CHECK ((seq = 1) = (prev_hash IS NULL))
);
CREATE TRIGGER platform_audit_seals_immutable BEFORE UPDATE OR DELETE ON platform_audit_seals FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();
CREATE TRIGGER platform_audit_seals_no_truncate BEFORE TRUNCATE ON platform_audit_seals FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_mutation();
ALTER TABLE platform_audit_seals ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_audit_seals FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_audit_seals_select ON platform_audit_seals FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_audit_seals_definer_select ON platform_audit_seals FOR SELECT TO artify_definer USING (true);
CREATE POLICY platform_audit_seals_definer_insert ON platform_audit_seals FOR INSERT TO artify_definer WITH CHECK (true);
GRANT SELECT ON platform_audit_seals TO artify_platform;
GRANT SELECT, INSERT ON platform_audit_seals TO artify_definer;

CREATE FUNCTION app.platform_audit_text(e platform_audit_events) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT concat_ws('|', e.id::text, e.actor_type, COALESCE(e.actor_id::text, ''), e.action, COALESCE(e.target_type, ''),
                   COALESCE(e.target_id, ''), COALESCE(e.target_organization::text, ''), e.outcome, COALESCE(e.reason, ''),
                   e.summary::text, to_char(e.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'))
$$;

CREATE FUNCTION app.platform_audit_seal(p_limit integer DEFAULT 1000) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE r platform_audit_events; last_seq bigint; last_hash bytea; n integer := 0; since timestamptz;
BEGIN
  IF p_limit < 1 OR p_limit > 10000 THEN
    RAISE EXCEPTION 'p_limit must be between 1 and 10000' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  PERFORM pg_advisory_xact_lock(7202602);
  SELECT COALESCE(max(audit_created_at), '-infinity'::timestamptz) INTO since FROM platform_audit_seals;
  IF since > '-infinity' THEN since := since - interval '1 hour'; END IF;
  FOR r IN SELECT e.* FROM platform_audit_events e
           WHERE e.created_at >= since AND NOT EXISTS (SELECT 1 FROM platform_audit_seals s WHERE s.audit_event_id = e.id)
           ORDER BY e.created_at, e.id LIMIT p_limit
  LOOP
    last_seq := NULL; last_hash := NULL;
    SELECT s.seq, s.row_hash INTO last_seq, last_hash FROM platform_audit_seals s ORDER BY s.seq DESC LIMIT 1;
    INSERT INTO platform_audit_seals (seq, audit_event_id, audit_created_at, prev_hash, row_hash)
    VALUES (COALESCE(last_seq, 0) + 1, r.id, r.created_at, last_hash,
            sha256(COALESCE(last_hash, ''::bytea) || convert_to(app.platform_audit_text(r), 'UTF8')));
    n := n + 1;
  END LOOP;
  RETURN n;
END $$;
ALTER FUNCTION app.platform_audit_seal(integer) OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.platform_audit_seal(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.platform_audit_seal(integer) TO artify_dispatcher;

CREATE FUNCTION app.platform_audit_verify()
RETURNS TABLE (checked bigint, ok boolean, first_bad_seq bigint, reason text, head_seq bigint, head_hash text)
LANGUAGE plpgsql STABLE AS $$
DECLARE s platform_audit_seals; ev platform_audit_events; prev_seq bigint := 0; prev_hash bytea := NULL; n bigint := 0; calc bytea;
BEGIN
  FOR s IN SELECT * FROM platform_audit_seals ORDER BY seq LOOP
    IF s.seq <> prev_seq + 1 THEN
      RETURN QUERY SELECT n, false, prev_seq + 1, 'sequence_gap'::text, prev_seq, encode(prev_hash, 'hex'); RETURN;
    END IF;
    IF s.prev_hash IS DISTINCT FROM prev_hash THEN
      RETURN QUERY SELECT n, false, s.seq, 'chain_broken'::text, prev_seq, encode(prev_hash, 'hex'); RETURN;
    END IF;
    SELECT * INTO ev FROM platform_audit_events e WHERE e.id = s.audit_event_id AND e.created_at = s.audit_created_at;
    IF NOT FOUND THEN
      RETURN QUERY SELECT n, false, s.seq, 'event_missing'::text, prev_seq, encode(prev_hash, 'hex'); RETURN;
    END IF;
    calc := sha256(COALESCE(prev_hash, ''::bytea) || convert_to(app.platform_audit_text(ev), 'UTF8'));
    IF calc <> s.row_hash THEN
      RETURN QUERY SELECT n, false, s.seq, 'event_modified'::text, prev_seq, encode(prev_hash, 'hex'); RETURN;
    END IF;
    n := n + 1; prev_seq := s.seq; prev_hash := s.row_hash;
  END LOOP;
  RETURN QUERY SELECT n, true, NULL::bigint, NULL::text, prev_seq, encode(prev_hash, 'hex');
END $$;
GRANT EXECUTE ON FUNCTION app.platform_audit_verify() TO artify_platform;

-- Heads for export (organization_id NULL = the platform chain) and the check of a previously exported head.
CREATE FUNCTION app.audit_heads() RETURNS TABLE (organization_id uuid, head_seq bigint, head_hash text, sealed_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT s.organization_id, s.seq, encode(s.row_hash, 'hex'), s.sealed_at
    FROM audit_seals s JOIN (SELECT audit_seals.organization_id AS o, max(audit_seals.seq) AS m FROM audit_seals GROUP BY 1) h
      ON h.o = s.organization_id AND h.m = s.seq
  UNION ALL
  SELECT NULL::uuid, p.seq, encode(p.row_hash, 'hex'), p.sealed_at FROM platform_audit_seals p
   WHERE p.seq = (SELECT max(seq) FROM platform_audit_seals)
$$;
ALTER FUNCTION app.audit_heads() OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.audit_heads() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.audit_heads() TO artify_dispatcher;

CREATE FUNCTION app.audit_seal_at(p_org uuid, p_seq bigint) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT CASE WHEN p_org IS NULL
              THEN (SELECT encode(row_hash, 'hex') FROM platform_audit_seals WHERE seq = p_seq)
              ELSE (SELECT encode(row_hash, 'hex') FROM audit_seals WHERE organization_id = p_org AND seq = p_seq) END
$$;
ALTER FUNCTION app.audit_seal_at(uuid, bigint) OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.audit_seal_at(uuid, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.audit_seal_at(uuid, bigint) TO artify_dispatcher;

-- Monitoring: how far behind the sealer is, without exposing any audit content. Executable by the platform realm (metrics scrape).
CREATE FUNCTION app.audit_seal_status()
RETURNS TABLE (tenant_unsealed bigint, platform_unsealed bigint, oldest_unsealed_seconds double precision)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT (SELECT count(*) FROM audit_events e WHERE e.organization_id IS NOT NULL AND e.created_at > now() - interval '7 days'
            AND NOT EXISTS (SELECT 1 FROM audit_seals s WHERE s.audit_event_id = e.id)),
         (SELECT count(*) FROM platform_audit_events e WHERE e.created_at > now() - interval '7 days'
            AND NOT EXISTS (SELECT 1 FROM platform_audit_seals s WHERE s.audit_event_id = e.id)),
         (SELECT extract(epoch FROM now() - min(u.created_at)) FROM (
            SELECT e.created_at FROM audit_events e WHERE e.organization_id IS NOT NULL AND e.created_at > now() - interval '7 days'
               AND NOT EXISTS (SELECT 1 FROM audit_seals s WHERE s.audit_event_id = e.id)
            UNION ALL
            SELECT e.created_at FROM platform_audit_events e WHERE e.created_at > now() - interval '7 days'
               AND NOT EXISTS (SELECT 1 FROM platform_audit_seals s WHERE s.audit_event_id = e.id)) u)
$$;
ALTER FUNCTION app.audit_seal_status() OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.audit_seal_status() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.audit_seal_status() TO artify_platform;
