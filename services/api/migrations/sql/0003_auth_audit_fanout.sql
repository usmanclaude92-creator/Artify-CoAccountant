-- Phase 1 follow-up: user-level authentication events (login, MFA, session revoke, password reset)
-- were stored without an organization, so tenant admins could not see them. They now fan out: one
-- unattributed row for the platform security stream, plus one tenant-visible copy per organization
-- the user belongs to (organization or company membership).
--
-- Written as a SECURITY DEFINER function because a user's request context only covers ONE
-- organization, so the app role could not insert copies for the others. To keep it from becoming a
-- way to forge history, it only accepts the currently authenticated subject (app.user_id()).

GRANT INSERT ON audit_events TO artify_definer;
CREATE POLICY definer_insert ON audit_events FOR INSERT TO artify_definer WITH CHECK (true);

CREATE FUNCTION app.audit_user_event(p_actor uuid, p_action text, p_summary jsonb, p_ip inet,
                                     p_ua text, p_cid text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF p_actor IS DISTINCT FROM app.user_id() THEN
    RAISE EXCEPTION 'audit_user_event only records events of the authenticated subject'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO audit_events (id, organization_id, actor_type, actor_id, action, entity_type, entity_id,
                            summary, ip, user_agent, correlation_id)
  VALUES (gen_random_uuid(), NULL, 'user', p_actor, p_action, 'user', p_actor::text,
          p_summary, p_ip, p_ua, p_cid);
  INSERT INTO audit_events (id, organization_id, actor_type, actor_id, action, entity_type, entity_id,
                            summary, ip, user_agent, correlation_id)
  SELECT gen_random_uuid(), o.organization_id, 'user', p_actor, p_action, 'user', p_actor::text,
         p_summary, p_ip, p_ua, p_cid
  FROM (SELECT organization_id FROM organization_memberships WHERE user_id = p_actor AND status = 'active'
        UNION
        SELECT organization_id FROM company_memberships WHERE user_id = p_actor AND status = 'active') o;
END $$;

ALTER FUNCTION app.audit_user_event(uuid, text, jsonb, inet, text, text) OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.audit_user_event(uuid, text, jsonb, inet, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.audit_user_event(uuid, text, jsonb, inet, text, text) TO artify_app;
