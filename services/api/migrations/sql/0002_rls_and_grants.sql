-- Phase 1: Row-Level Security, grants, immutability triggers, narrow SECURITY DEFINER functions.
-- Design: docs/database/ISOLATION_AND_RLS.md. Fail closed: missing context => no rows.
--
-- Transaction context (set per transaction with set_config(..., true) by the API):
--   app.user_id        authenticated subject (user id, or platform admin id in the platform realm)
--   app.org_id         active Organization, validated against memberships server-side
--   app.company_scope  'all' | 'list'
--   app.company_ids    comma-separated company ids when scope = 'list'

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'artify_definer') THEN
    CREATE ROLE artify_definer NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO artify_app, artify_platform, artify_dispatcher, artify_definer;
GRANT USAGE, CREATE ON SCHEMA app TO artify_definer;
GRANT USAGE ON SCHEMA app TO artify_app, artify_platform, artify_dispatcher;

-- ------------------------------------------------------------------ context helpers
CREATE FUNCTION app.org_id() RETURNS uuid LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$;

CREATE FUNCTION app.user_id() RETURNS uuid LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;

CREATE FUNCTION app.company_scope() RETURNS text LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.company_scope', true), '') $$;

CREATE FUNCTION app.can_see_company(org uuid, comp uuid) RETURNS boolean LANGUAGE sql STABLE
  AS $$
    SELECT org IS NOT NULL AND comp IS NOT NULL AND org = app.org_id()
       AND ( app.company_scope() = 'all'
          OR comp = ANY (string_to_array(NULLIF(current_setting('app.company_ids', true), ''), ',')::uuid[]) )
  $$;

-- ------------------------------------------------------------------ immutability
CREATE FUNCTION app.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% on % is not permitted: table is append-only', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'integrity_constraint_violation';
END $$;

CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_mutation();
CREATE TRIGGER platform_audit_events_immutable BEFORE UPDATE OR DELETE ON platform_audit_events
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();
CREATE TRIGGER platform_audit_events_no_truncate BEFORE TRUNCATE ON platform_audit_events
  FOR EACH STATEMENT EXECUTE FUNCTION app.forbid_mutation();

-- ------------------------------------------------------------------ integrity triggers
CREATE FUNCTION app.check_assignment_member() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM organization_memberships m JOIN roles r ON r.id = m.role_id
    WHERE m.organization_id = NEW.organization_id AND m.user_id = NEW.user_id
      AND m.status = 'active' AND r.key IN ('accountant', 'staff')
  ) THEN
    RAISE EXCEPTION 'user % is not an active accountant/staff member of the organization', NEW.user_id
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER accountant_assignments_member BEFORE INSERT OR UPDATE ON accountant_assignments
  FOR EACH ROW EXECUTE FUNCTION app.check_assignment_member();

CREATE FUNCTION app.check_role_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  expected text := TG_ARGV[0];
  v_scope text; v_org uuid;
BEGIN
  IF TG_TABLE_NAME = 'invitations' THEN
    -- exactly one of the two role columns is set (table CHECK); validate whichever is present
    IF NEW.org_role_id IS NOT NULL THEN
      SELECT scope, organization_id INTO v_scope, v_org FROM roles WHERE id = NEW.org_role_id;
      IF v_scope IS DISTINCT FROM 'organization' THEN
        RAISE EXCEPTION 'invitation org_role_id must reference an organization-scoped role'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    ELSE
      SELECT scope, organization_id INTO v_scope, v_org FROM roles WHERE id = NEW.company_role_id;
      IF v_scope IS DISTINCT FROM 'company' THEN
        RAISE EXCEPTION 'invitation company_role_id must reference a company-scoped role'
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
    END IF;
  ELSE
    SELECT scope, organization_id INTO v_scope, v_org FROM roles WHERE id = NEW.role_id;
    IF v_scope IS DISTINCT FROM expected THEN
      RAISE EXCEPTION 'role scope % required on %', expected, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  IF v_org IS NOT NULL AND v_org IS DISTINCT FROM NEW.organization_id THEN
    RAISE EXCEPTION 'custom role belongs to another organization'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER org_memberships_role_scope BEFORE INSERT OR UPDATE ON organization_memberships
  FOR EACH ROW EXECUTE FUNCTION app.check_role_scope('organization');
CREATE TRIGGER company_memberships_role_scope BEFORE INSERT OR UPDATE ON company_memberships
  FOR EACH ROW EXECUTE FUNCTION app.check_role_scope('company');
CREATE TRIGGER invitations_role_scope BEFORE INSERT OR UPDATE ON invitations
  FOR EACH ROW EXECUTE FUNCTION app.check_role_scope('invitation');

-- ------------------------------------------------------------------ narrow SECURITY DEFINER functions
-- Pre-authentication lookups cannot satisfy per-user RLS (there is no user yet). Each function
-- returns the minimum for exactly one key, is owned by artify_definer (which has explicit,
-- table-by-table policies below, not BYPASSRLS) and pins its search_path.

CREATE FUNCTION app.auth_user_by_email(p_email citext)
RETURNS TABLE (id uuid, email citext, display_name text, locale text, status text,
               email_verified_at timestamptz, password_hash text, mfa_enabled boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT u.id, u.email, u.display_name, u.locale, u.status, u.email_verified_at, c.password_hash,
         EXISTS (SELECT 1 FROM mfa_factors f WHERE f.user_id = u.id AND f.confirmed_at IS NOT NULL)
  FROM users u LEFT JOIN auth_credentials c ON c.user_id = u.id
  WHERE u.email = p_email
$$;

CREATE FUNCTION app.auth_platform_admin_by_email(p_email citext)
RETURNS TABLE (id uuid, email citext, display_name text, role_key text, status text,
               password_hash text, mfa_enabled boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT a.id, a.email, a.display_name, a.role_key, a.status, a.password_hash,
         EXISTS (SELECT 1 FROM mfa_factors f WHERE f.platform_admin_id = a.id AND f.confirmed_at IS NOT NULL)
  FROM platform_admins a WHERE a.email = p_email
$$;

CREATE FUNCTION app.auth_refresh_token_by_hash(p_hash bytea)
RETURNS TABLE (token_id uuid, session_id uuid, realm text, user_id uuid, platform_admin_id uuid,
               token_used_at timestamptz, token_expires_at timestamptz,
               session_expires_at timestamptz, session_revoked_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT t.id, s.id, s.realm, s.user_id, s.platform_admin_id, t.used_at, t.expires_at,
         s.expires_at, s.revoked_at
  FROM refresh_tokens t JOIN sessions s ON s.id = t.session_id
  WHERE t.token_hash = p_hash
$$;

CREATE FUNCTION app.auth_email_token_by_hash(p_hash bytea)
RETURNS TABLE (id uuid, user_id uuid, purpose text, expires_at timestamptz, used_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT t.id, t.user_id, t.purpose, t.expires_at, t.used_at FROM email_tokens t WHERE t.token_hash = p_hash
$$;

CREATE FUNCTION app.auth_invitation_by_hash(p_hash bytea)
RETURNS TABLE (id uuid, organization_id uuid, email citext, org_role_id uuid, company_id uuid,
               company_role_id uuid, expires_at timestamptz, accepted_at timestamptz, revoked_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT i.id, i.organization_id, i.email, i.org_role_id, i.company_id, i.company_role_id,
         i.expires_at, i.accepted_at, i.revoked_at
  FROM invitations i WHERE i.token_hash = p_hash
$$;

-- Tenants never write subscriptions directly; signup gets a trial on the platform-configured plan.
CREATE FUNCTION app.create_trial_subscription(p_id uuid, p_org uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
  v_plan_key text; v_version uuid; v_days integer;
BEGIN
  IF p_org IS DISTINCT FROM app.org_id() THEN
    RAISE EXCEPTION 'organization context mismatch' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT value #>> '{}' INTO v_plan_key FROM platform_settings WHERE key = 'default_plan_key';
  IF v_plan_key IS NULL THEN
    RAISE EXCEPTION 'platform setting default_plan_key is not configured' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  SELECT v.id INTO v_version FROM subscription_plan_versions v
    JOIN subscription_plans p ON p.id = v.plan_id
   WHERE p.key = v_plan_key AND p.status = 'active' ORDER BY v.version DESC LIMIT 1;
  IF v_version IS NULL THEN
    RAISE EXCEPTION 'default plan % has no version', v_plan_key USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  SELECT COALESCE((value #>> '{}')::integer, 30) INTO v_days FROM platform_settings WHERE key = 'trial_days';
  v_days := COALESCE(v_days, 30);
  INSERT INTO subscriptions (id, organization_id, plan_version_id, status, current_period_start, trial_ends_at)
  VALUES (p_id, p_org, v_version, 'trialing', now(), now() + make_interval(days => v_days));
  RETURN p_id;
END $$;


-- Entitlement measurement must count ALL of an organization's rows, which per-caller RLS would
-- under-report for scoped users (e.g. an accountant). It is read-only, bound to the active org.
CREATE FUNCTION app.measure_entitlement(p_org uuid, p_key text) RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v bigint;
BEGIN
  IF p_org IS DISTINCT FROM app.org_id() THEN
    RAISE EXCEPTION 'organization context mismatch' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_key = 'limit.companies' THEN
    SELECT count(*) INTO v FROM companies WHERE organization_id = p_org AND status <> 'archived';
  ELSIF p_key = 'limit.users' THEN
    SELECT count(DISTINCT u) INTO v FROM (
      SELECT user_id AS u FROM organization_memberships WHERE organization_id = p_org AND status = 'active'
      UNION
      SELECT user_id FROM company_memberships WHERE organization_id = p_org AND status = 'active') x;
  ELSIF p_key = 'limit.accountants' THEN
    SELECT count(*) INTO v FROM organization_memberships m JOIN roles r ON r.id = m.role_id
     WHERE m.organization_id = p_org AND m.status = 'active' AND r.key IN ('accountant', 'staff');
  ELSIF p_key = 'limit.devices' THEN
    SELECT count(*) INTO v FROM devices WHERE organization_id = p_org AND status = 'active';
  ELSE
    RAISE EXCEPTION 'entitlement % is not measurable yet', p_key USING ERRCODE = 'feature_not_supported';
  END IF;
  RETURN v;
END $$;

DO $$
DECLARE f text;
BEGIN
  FOREACH f IN ARRAY ARRAY[
    'auth_user_by_email(citext)', 'auth_platform_admin_by_email(citext)',
    'auth_refresh_token_by_hash(bytea)', 'auth_email_token_by_hash(bytea)',
    'auth_invitation_by_hash(bytea)', 'create_trial_subscription(uuid, uuid)',
    'measure_entitlement(uuid, text)']
  LOOP
    EXECUTE format('ALTER FUNCTION app.%s OWNER TO artify_definer', f);
    EXECUTE format('REVOKE ALL ON FUNCTION app.%s FROM PUBLIC', f);
  END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION app.auth_user_by_email(citext), app.auth_refresh_token_by_hash(bytea),
  app.auth_email_token_by_hash(bytea), app.auth_invitation_by_hash(bytea),
  app.create_trial_subscription(uuid, uuid), app.measure_entitlement(uuid, text) TO artify_app;
GRANT EXECUTE ON FUNCTION app.auth_platform_admin_by_email(citext),
  app.auth_refresh_token_by_hash(bytea) TO artify_platform;

-- ------------------------------------------------------------------ enable + force RLS
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'subscription_plans', 'subscription_plan_versions', 'plan_entitlements', 'platform_settings',
    'platform_admins', 'platform_audit_events',
    'organizations', 'users', 'auth_credentials', 'mfa_factors', 'mfa_recovery_codes', 'devices',
    'sessions', 'refresh_tokens', 'email_tokens', 'roles', 'organization_memberships', 'companies',
    'company_memberships', 'accountant_assignments', 'invitations',
    'subscriptions', 'entitlement_overrides', 'usage_counters', 'usage_records',
    'support_access_grants', 'retention_policies', 'audit_events', 'outbox_events', 'idempotency_keys']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

-- ------------------------------------------------------------------ grants (least privilege)
GRANT SELECT ON currencies, entitlement_definitions, permissions, role_permissions
  TO artify_app, artify_platform;
GRANT SELECT ON roles, retention_policies, subscriptions, entitlement_overrides, usage_records,
  support_access_grants, subscription_plans, subscription_plan_versions, plan_entitlements TO artify_app;
GRANT SELECT, INSERT, UPDATE ON organizations, users, auth_credentials, mfa_factors, mfa_recovery_codes,
  devices, sessions, refresh_tokens, email_tokens, organization_memberships, companies,
  company_memberships, invitations, usage_counters, idempotency_keys TO artify_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON accountant_assignments TO artify_app;
GRANT SELECT, INSERT ON usage_records, audit_events, outbox_events TO artify_app;

GRANT SELECT ON organizations, roles, retention_policies, subscriptions, entitlement_overrides,
  usage_counters, usage_records, support_access_grants TO artify_platform;
GRANT SELECT, INSERT, UPDATE ON subscription_plans, subscription_plan_versions, plan_entitlements,
  platform_settings, sessions, refresh_tokens, mfa_factors, mfa_recovery_codes TO artify_platform;
GRANT SELECT, UPDATE ON platform_admins TO artify_platform;
GRANT SELECT, INSERT ON platform_audit_events TO artify_platform;
GRANT SELECT ON audit_events TO artify_platform;

GRANT SELECT, UPDATE ON outbox_events TO artify_dispatcher;

GRANT SELECT ON users, auth_credentials, mfa_factors, platform_admins, refresh_tokens, sessions,
  email_tokens, invitations, platform_settings, subscription_plans, subscription_plan_versions,
  organization_memberships, roles, companies, company_memberships, devices TO artify_definer;
GRANT INSERT ON subscriptions TO artify_definer;

-- ------------------------------------------------------------------ policies: definer (explicit, read-only except subscriptions)
CREATE POLICY definer_read ON users                       FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON auth_credentials            FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON mfa_factors                 FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON platform_admins             FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON refresh_tokens              FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON sessions                    FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON email_tokens                FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON invitations                 FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON platform_settings           FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON subscription_plans          FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON subscription_plan_versions  FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON organization_memberships    FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON roles                       FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON companies                   FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON company_memberships         FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON devices                     FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_insert ON subscriptions             FOR INSERT TO artify_definer WITH CHECK (status = 'trialing');

-- ------------------------------------------------------------------ policies: tenant realm (artify_app)
-- Organizations: the active org, plus orgs where the caller holds a membership (org picker).
CREATE POLICY org_select ON organizations FOR SELECT TO artify_app USING (
  id = app.org_id()
  OR EXISTS (SELECT 1 FROM organization_memberships m WHERE m.organization_id = organizations.id
             AND m.user_id = app.user_id() AND m.status = 'active')
  OR EXISTS (SELECT 1 FROM company_memberships c WHERE c.organization_id = organizations.id
             AND c.user_id = app.user_id() AND c.status = 'active'));
CREATE POLICY org_insert ON organizations FOR INSERT TO artify_app WITH CHECK (id = app.org_id());
CREATE POLICY org_update ON organizations FOR UPDATE TO artify_app
  USING (id = app.org_id()) WITH CHECK (id = app.org_id());

CREATE POLICY users_select ON users FOR SELECT TO artify_app USING (
  id = app.user_id()
  OR EXISTS (SELECT 1 FROM organization_memberships m WHERE m.user_id = users.id
             AND m.organization_id = app.org_id())
  OR EXISTS (SELECT 1 FROM company_memberships c WHERE c.user_id = users.id
             AND app.can_see_company(c.organization_id, c.company_id)));
CREATE POLICY users_insert ON users FOR INSERT TO artify_app WITH CHECK (id = app.user_id());
CREATE POLICY users_update ON users FOR UPDATE TO artify_app
  USING (id = app.user_id()) WITH CHECK (id = app.user_id());

CREATE POLICY own_rows ON auth_credentials     FOR ALL TO artify_app USING (user_id = app.user_id()) WITH CHECK (user_id = app.user_id());
CREATE POLICY own_rows ON mfa_factors          FOR ALL TO artify_app USING (user_id = app.user_id()) WITH CHECK (user_id = app.user_id());
CREATE POLICY own_rows ON mfa_recovery_codes   FOR ALL TO artify_app USING (user_id = app.user_id()) WITH CHECK (user_id = app.user_id());
CREATE POLICY own_rows ON email_tokens         FOR ALL TO artify_app USING (user_id = app.user_id()) WITH CHECK (user_id = app.user_id());
CREATE POLICY own_rows ON devices              FOR ALL TO artify_app USING (user_id = app.user_id()) WITH CHECK (user_id = app.user_id());
CREATE POLICY own_rows ON sessions             FOR ALL TO artify_app USING (user_id = app.user_id()) WITH CHECK (user_id = app.user_id());
CREATE POLICY own_rows ON refresh_tokens       FOR ALL TO artify_app
  USING (EXISTS (SELECT 1 FROM sessions s WHERE s.id = refresh_tokens.session_id AND s.user_id = app.user_id()))
  WITH CHECK (EXISTS (SELECT 1 FROM sessions s WHERE s.id = refresh_tokens.session_id AND s.user_id = app.user_id()));
CREATE POLICY own_rows ON idempotency_keys     FOR ALL TO artify_app USING (user_id = app.user_id()) WITH CHECK (user_id = app.user_id());

CREATE POLICY roles_select ON roles FOR SELECT TO artify_app
  USING (organization_id IS NULL OR organization_id = app.org_id());

CREATE POLICY memberships_select ON organization_memberships FOR SELECT TO artify_app
  USING (organization_id = app.org_id() OR user_id = app.user_id());
CREATE POLICY memberships_insert ON organization_memberships FOR INSERT TO artify_app
  WITH CHECK (organization_id = app.org_id());
CREATE POLICY memberships_update ON organization_memberships FOR UPDATE TO artify_app
  USING (organization_id = app.org_id()) WITH CHECK (organization_id = app.org_id());

CREATE POLICY companies_select ON companies FOR SELECT TO artify_app
  USING (app.can_see_company(organization_id, id));
CREATE POLICY companies_insert ON companies FOR INSERT TO artify_app
  WITH CHECK (organization_id = app.org_id() AND app.company_scope() = 'all');
CREATE POLICY companies_update ON companies FOR UPDATE TO artify_app
  USING (app.can_see_company(organization_id, id)) WITH CHECK (app.can_see_company(organization_id, id));

CREATE POLICY cm_select ON company_memberships FOR SELECT TO artify_app
  USING (app.can_see_company(organization_id, company_id) OR user_id = app.user_id());
CREATE POLICY cm_insert ON company_memberships FOR INSERT TO artify_app
  WITH CHECK (app.can_see_company(organization_id, company_id));
CREATE POLICY cm_update ON company_memberships FOR UPDATE TO artify_app
  USING (app.can_see_company(organization_id, company_id))
  WITH CHECK (app.can_see_company(organization_id, company_id));

CREATE POLICY aa_select ON accountant_assignments FOR SELECT TO artify_app
  USING (app.can_see_company(organization_id, company_id) OR user_id = app.user_id());
CREATE POLICY aa_write ON accountant_assignments FOR ALL TO artify_app
  USING (app.can_see_company(organization_id, company_id))
  WITH CHECK (app.can_see_company(organization_id, company_id));

CREATE POLICY inv_all ON invitations FOR ALL TO artify_app
  USING (organization_id = app.org_id())
  WITH CHECK (organization_id = app.org_id()
              AND (company_id IS NULL OR app.can_see_company(organization_id, company_id)));
-- (the select above overlaps definer_read by design; app code never reads invitations without org context)

CREATE POLICY sub_select ON subscriptions FOR SELECT TO artify_app USING (organization_id = app.org_id());
CREATE POLICY ovr_select ON entitlement_overrides FOR SELECT TO artify_app USING (organization_id = app.org_id());
CREATE POLICY uc_all ON usage_counters FOR ALL TO artify_app
  USING (organization_id = app.org_id()) WITH CHECK (organization_id = app.org_id());
CREATE POLICY ur_select ON usage_records FOR SELECT TO artify_app USING (organization_id = app.org_id());
CREATE POLICY ur_insert ON usage_records FOR INSERT TO artify_app WITH CHECK (organization_id = app.org_id());
CREATE POLICY sag_select ON support_access_grants FOR SELECT TO artify_app USING (organization_id = app.org_id());
CREATE POLICY rp_select ON retention_policies FOR SELECT TO artify_app
  USING (organization_id IS NULL OR organization_id = app.org_id());

-- A tenant can read only the plan (version, entitlements) its own subscription is pinned to.
CREATE POLICY plans_select ON subscription_plan_versions FOR SELECT TO artify_app USING (
  EXISTS (SELECT 1 FROM subscriptions s WHERE s.plan_version_id = subscription_plan_versions.id
          AND s.organization_id = app.org_id()));
CREATE POLICY plans_select ON subscription_plans FOR SELECT TO artify_app USING (
  EXISTS (SELECT 1 FROM subscription_plan_versions v JOIN subscriptions s ON s.plan_version_id = v.id
          WHERE v.plan_id = subscription_plans.id AND s.organization_id = app.org_id()));
CREATE POLICY plans_select ON plan_entitlements FOR SELECT TO artify_app USING (
  EXISTS (SELECT 1 FROM subscriptions s WHERE s.plan_version_id = plan_entitlements.plan_version_id
          AND s.organization_id = app.org_id()));

CREATE POLICY audit_insert ON audit_events FOR INSERT TO artify_app WITH CHECK (
  organization_id IS NULL
  OR (organization_id = app.org_id() AND (company_id IS NULL OR app.can_see_company(organization_id, company_id))));
CREATE POLICY audit_select ON audit_events FOR SELECT TO artify_app USING (
  organization_id = app.org_id() AND (company_id IS NULL OR app.can_see_company(organization_id, company_id)));

CREATE POLICY outbox_insert ON outbox_events FOR INSERT TO artify_app
  WITH CHECK (organization_id IS NULL OR organization_id = app.org_id());
CREATE POLICY outbox_select ON outbox_events FOR SELECT TO artify_app USING (organization_id = app.org_id());
CREATE POLICY outbox_dispatch_select ON outbox_events FOR SELECT TO artify_dispatcher USING (true);
CREATE POLICY outbox_dispatch_update ON outbox_events FOR UPDATE TO artify_dispatcher USING (true) WITH CHECK (true);

-- ------------------------------------------------------------------ policies: platform realm (artify_platform)
-- Deliberately NO policy on companies, memberships, documents or any business table: the
-- Super Admin surface cannot read customer business data. Support grants (Phase 2) add narrowly
-- scoped, time-limited policies on top.
CREATE POLICY platform_read ON organizations           FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_read ON roles                   FOR SELECT TO artify_platform USING (organization_id IS NULL);
CREATE POLICY platform_read ON retention_policies      FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_read ON subscriptions           FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_read ON entitlement_overrides   FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_read ON usage_counters          FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_read ON usage_records           FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_read ON support_access_grants   FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_all ON subscription_plans         FOR ALL TO artify_platform USING (true) WITH CHECK (true);
CREATE POLICY platform_all ON subscription_plan_versions FOR ALL TO artify_platform USING (true) WITH CHECK (true);
CREATE POLICY platform_all ON plan_entitlements          FOR ALL TO artify_platform USING (true) WITH CHECK (true);
CREATE POLICY platform_all ON platform_settings          FOR ALL TO artify_platform USING (true) WITH CHECK (true);
CREATE POLICY platform_admin_self ON platform_admins FOR ALL TO artify_platform
  USING (id = app.user_id()) WITH CHECK (id = app.user_id());
-- Security events that cannot be attributed to a tenant (failed logins, denied cross-tenant
-- attempts) carry organization_id NULL and are readable by the platform only.
CREATE POLICY platform_unattributed_audit ON audit_events FOR SELECT TO artify_platform
  USING (organization_id IS NULL);
CREATE POLICY platform_audit_select ON platform_audit_events FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_audit_insert ON platform_audit_events FOR INSERT TO artify_platform WITH CHECK (true);
CREATE POLICY platform_own_rows ON sessions FOR ALL TO artify_platform
  USING (platform_admin_id = app.user_id()) WITH CHECK (platform_admin_id = app.user_id());
CREATE POLICY platform_own_rows ON refresh_tokens FOR ALL TO artify_platform
  USING (EXISTS (SELECT 1 FROM sessions s WHERE s.id = refresh_tokens.session_id AND s.platform_admin_id = app.user_id()))
  WITH CHECK (EXISTS (SELECT 1 FROM sessions s WHERE s.id = refresh_tokens.session_id AND s.platform_admin_id = app.user_id()));
CREATE POLICY platform_own_rows ON mfa_factors FOR ALL TO artify_platform
  USING (platform_admin_id = app.user_id()) WITH CHECK (platform_admin_id = app.user_id());
CREATE POLICY platform_own_rows ON mfa_recovery_codes FOR ALL TO artify_platform
  USING (platform_admin_id = app.user_id()) WITH CHECK (platform_admin_id = app.user_id());
