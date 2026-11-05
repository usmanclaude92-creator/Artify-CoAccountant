-- Phase 12 (continued): tamper-evident audit log. A system job (NOT the request path) seals each tenant audit event into a
-- per-organization hash chain: row_hash = sha256(previous row_hash || canonical text of the event). Rewriting or deleting an
-- event, or removing/reordering a seal, breaks the chain and `app.audit_verify` reports where.
--
-- What this proves and does not: it detects changes to sealed history by anyone who cannot also recompute the whole chain.
-- A database owner who can rewrite audit_events AND audit_seals can do both, and cutting off the newest seals is invisible
-- unless the head hash was copied somewhere the owner cannot reach. Exporting `head_hash` regularly to an external store is
-- the missing control. Events with no organization (pre-tenant security events) are not sealed.

CREATE TABLE audit_seals (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id  uuid NOT NULL REFERENCES organizations (id),
  seq              bigint NOT NULL CHECK (seq >= 1),
  audit_event_id   uuid NOT NULL,
  audit_created_at timestamptz NOT NULL,
  prev_hash        bytea,
  row_hash         bytea NOT NULL,
  sealed_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, seq),
  UNIQUE (audit_event_id),
  CHECK ((seq = 1) = (prev_hash IS NULL))
);
CREATE INDEX audit_seals_created ON audit_seals (audit_created_at);

CREATE TRIGGER audit_seals_immutable BEFORE UPDATE OR DELETE ON audit_seals FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();
CREATE TRIGGER audit_seals_no_truncate BEFORE TRUNCATE ON audit_seals FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_mutation();

ALTER TABLE audit_seals ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_seals FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_seals_tenant_select ON audit_seals FOR SELECT TO artify_app USING (organization_id = app.org_id());
CREATE POLICY audit_seals_definer_select ON audit_seals FOR SELECT TO artify_definer USING (true);
CREATE POLICY audit_seals_definer_insert ON audit_seals FOR INSERT TO artify_definer WITH CHECK (true);
GRANT SELECT ON audit_seals TO artify_app;
GRANT SELECT, INSERT ON audit_seals TO artify_definer;
GRANT SELECT ON audit_events TO artify_definer;
CREATE POLICY definer_select_audit ON audit_events FOR SELECT TO artify_definer USING (true);

-- The exact text that is hashed. Every field is positional and NULL-safe.
CREATE FUNCTION app.audit_text(e audit_events) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT concat_ws('|', e.id::text, COALESCE(e.organization_id::text, ''), COALESCE(e.company_id::text, ''), e.actor_type,
                   COALESCE(e.actor_id::text, ''), e.action, COALESCE(e.entity_type, ''), COALESCE(e.entity_id, ''),
                   e.summary::text, to_char(e.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'))
$$;

-- Seals up to p_limit unsealed events (oldest first). One sealer at a time. Looks back one hour from the newest seal so an
-- event whose transaction committed late is still picked up.
CREATE FUNCTION app.audit_seal(p_limit integer DEFAULT 1000) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE r audit_events; last_seq bigint; last_hash bytea; n integer := 0; since timestamptz;
BEGIN
  IF p_limit < 1 OR p_limit > 10000 THEN
    RAISE EXCEPTION 'p_limit must be between 1 and 10000' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  PERFORM pg_advisory_xact_lock(7202601);
  SELECT COALESCE(max(audit_created_at), '-infinity'::timestamptz) INTO since FROM audit_seals;
  IF since > '-infinity' THEN since := since - interval '1 hour'; END IF;
  FOR r IN SELECT e.* FROM audit_events e
           WHERE e.organization_id IS NOT NULL AND e.created_at >= since
             AND NOT EXISTS (SELECT 1 FROM audit_seals s WHERE s.audit_event_id = e.id)
           ORDER BY e.created_at, e.id LIMIT p_limit
  LOOP
    last_seq := NULL; last_hash := NULL;
    SELECT s.seq, s.row_hash INTO last_seq, last_hash FROM audit_seals s WHERE s.organization_id = r.organization_id ORDER BY s.seq DESC LIMIT 1;
    INSERT INTO audit_seals (organization_id, seq, audit_event_id, audit_created_at, prev_hash, row_hash)
    VALUES (r.organization_id, COALESCE(last_seq, 0) + 1, r.id, r.created_at, last_hash,
            sha256(COALESCE(last_hash, ''::bytea) || convert_to(app.audit_text(r), 'UTF8')));
    n := n + 1;
  END LOOP;
  RETURN n;
END $$;
ALTER FUNCTION app.audit_seal(integer) OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.audit_seal(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.audit_seal(integer) TO artify_dispatcher;
GRANT USAGE ON SCHEMA app TO artify_definer;

-- Recomputes the chain of one organization under the CALLER's row-level security (a tenant sees only its own rows).
CREATE FUNCTION app.audit_verify(p_org uuid)
RETURNS TABLE (checked bigint, ok boolean, first_bad_seq bigint, reason text, head_seq bigint, head_hash text)
LANGUAGE plpgsql STABLE AS $$
DECLARE s audit_seals; ev audit_events; prev_seq bigint := 0; prev_hash bytea := NULL; n bigint := 0; calc bytea;
BEGIN
  FOR s IN SELECT * FROM audit_seals WHERE organization_id = p_org ORDER BY seq LOOP
    IF s.seq <> prev_seq + 1 THEN
      RETURN QUERY SELECT n, false, prev_seq + 1, 'sequence_gap'::text, prev_seq, encode(prev_hash, 'hex'); RETURN;
    END IF;
    IF s.prev_hash IS DISTINCT FROM prev_hash THEN
      RETURN QUERY SELECT n, false, s.seq, 'chain_broken'::text, prev_seq, encode(prev_hash, 'hex'); RETURN;
    END IF;
    SELECT * INTO ev FROM audit_events e WHERE e.id = s.audit_event_id AND e.created_at = s.audit_created_at;
    IF NOT FOUND THEN
      RETURN QUERY SELECT n, false, s.seq, 'event_missing'::text, prev_seq, encode(prev_hash, 'hex'); RETURN;
    END IF;
    calc := sha256(COALESCE(prev_hash, ''::bytea) || convert_to(app.audit_text(ev), 'UTF8'));
    IF calc <> s.row_hash THEN
      RETURN QUERY SELECT n, false, s.seq, 'event_modified'::text, prev_seq, encode(prev_hash, 'hex'); RETURN;
    END IF;
    n := n + 1; prev_seq := s.seq; prev_hash := s.row_hash;
  END LOOP;
  RETURN QUERY SELECT n, true, NULL::bigint, NULL::text, prev_seq, encode(prev_hash, 'hex');
END $$;
GRANT EXECUTE ON FUNCTION app.audit_verify(uuid) TO artify_app;
