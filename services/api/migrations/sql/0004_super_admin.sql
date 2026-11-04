-- Phase 2: Artify Super Admin.
-- Principles kept from Phase 1: the platform realm (artify_platform) has NO direct policy on tenant
-- business tables. Cross-tenant overviews are exposed through definer-owned aggregate VIEWS that
-- contain no financial data; deeper (support) access exists only through a customer-approved,
-- time-limited grant (support_access_grants) evaluated inside RLS.

-- ------------------------------------------------------------------ platform roles
ALTER TABLE platform_admins DROP CONSTRAINT platform_admins_role_key_check;
ALTER TABLE platform_admins ADD CONSTRAINT platform_admins_role_key_check CHECK (role_key IN
  ('platform_owner', 'platform_admin', 'platform_support', 'platform_billing', 'platform_auditor', 'platform_readonly'));
ALTER TABLE platform_admins ADD COLUMN invited_by uuid REFERENCES platform_admins (id);

CREATE TABLE platform_admin_invitations (
  id          uuid PRIMARY KEY,
  email       citext NOT NULL,
  role_key    text NOT NULL CHECK (role_key IN
                ('platform_owner', 'platform_admin', 'platform_support', 'platform_billing', 'platform_auditor', 'platform_readonly')),
  token_hash  bytea NOT NULL UNIQUE,
  invited_by  uuid NOT NULL REFERENCES platform_admins (id),
  expires_at  timestamptz NOT NULL,
  accepted_at timestamptz,
  revoked_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX platform_admin_invitations_email ON platform_admin_invitations (email);

-- ------------------------------------------------------------------ organization lifecycle history
CREATE TABLE organization_status_events (
  id               uuid PRIMARY KEY,
  organization_id  uuid NOT NULL REFERENCES organizations (id),
  from_status      text NOT NULL,
  to_status        text NOT NULL,
  reason           text NOT NULL CHECK (length(reason) >= 10),
  actor_id         uuid NOT NULL REFERENCES platform_admins (id),
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX organization_status_events_org ON organization_status_events (organization_id, created_at DESC);

-- ------------------------------------------------------------------ plans and subscriptions
ALTER TABLE subscription_plans DROP CONSTRAINT subscription_plans_status_check;
ALTER TABLE subscription_plans ADD CONSTRAINT subscription_plans_status_check
  CHECK (status IN ('active', 'inactive', 'retired'));      -- retired = archived
ALTER TABLE subscription_plans ADD COLUMN description text;

ALTER TABLE subscription_plan_versions
  ADD COLUMN price_amount   numeric(18, 3) CHECK (price_amount >= 0),
  ADD COLUMN price_currency char(3) REFERENCES currencies (code),
  ADD COLUMN support_level  text NOT NULL DEFAULT 'standard'
                              CHECK (support_level IN ('community', 'standard', 'priority', 'dedicated')),
  ADD COLUMN trial_days     integer CHECK (trial_days >= 0),
  ADD COLUMN notes          text,
  ADD COLUMN created_by     uuid REFERENCES platform_admins (id),
  ADD CONSTRAINT plan_version_price_pair CHECK ((price_amount IS NULL) = (price_currency IS NULL));

ALTER TABLE subscriptions DROP CONSTRAINT subscriptions_status_check;
ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_status_check
  CHECK (status IN ('trialing', 'active', 'past_due', 'suspended', 'cancelled', 'expired'));
ALTER TABLE subscriptions
  ADD COLUMN cancel_at_period_end boolean NOT NULL DEFAULT false,
  ADD COLUMN cancelled_at         timestamptz;

CREATE TABLE subscription_events (
  id                    uuid PRIMARY KEY,
  subscription_id       uuid NOT NULL REFERENCES subscriptions (id),
  organization_id       uuid NOT NULL REFERENCES organizations (id),
  event_type            text NOT NULL,
  from_status           text,
  to_status             text,
  from_plan_version_id  uuid REFERENCES subscription_plan_versions (id),
  to_plan_version_id    uuid REFERENCES subscription_plan_versions (id),
  reason                text,
  actor_type            text NOT NULL CHECK (actor_type IN ('platform_admin', 'system')),
  actor_id              uuid,
  details               jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX subscription_events_org ON subscription_events (organization_id, created_at DESC);
CREATE INDEX subscription_events_time ON subscription_events (created_at DESC);

INSERT INTO subscription_events (id, subscription_id, organization_id, event_type, to_status, to_plan_version_id,
                                 actor_type, created_at)
SELECT gen_random_uuid(), id, organization_id, 'created', status, plan_version_id, 'system', created_at
  FROM subscriptions;

-- A plan version that any subscription uses is immutable (grandfathering: edits create a new version).
CREATE FUNCTION app.guard_pinned_plan_version() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v uuid;
BEGIN
  IF TG_TABLE_NAME = 'subscription_plan_versions' THEN
    v := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
  ELSE
    v := CASE WHEN TG_OP = 'DELETE' THEN OLD.plan_version_id ELSE NEW.plan_version_id END;
  END IF;
  IF EXISTS (SELECT 1 FROM subscriptions WHERE plan_version_id = v) THEN
    RAISE EXCEPTION 'plan version % is in use by a subscription and is immutable; create a new version', v
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER plan_entitlements_pinned BEFORE INSERT OR UPDATE OR DELETE ON plan_entitlements
  FOR EACH ROW EXECUTE FUNCTION app.guard_pinned_plan_version();
CREATE TRIGGER plan_versions_pinned BEFORE UPDATE OR DELETE ON subscription_plan_versions
  FOR EACH ROW EXECUTE FUNCTION app.guard_pinned_plan_version();

CREATE TRIGGER subscription_events_immutable BEFORE UPDATE OR DELETE ON subscription_events
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();
CREATE TRIGGER organization_status_events_immutable BEFORE UPDATE OR DELETE ON organization_status_events
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();

-- ------------------------------------------------------------------ billing (provider-independent, manual records)
-- No card data and no provider credentials are ever stored. `provider`/`provider_reference` stay NULL
-- until a billing provider is chosen (decision D-2). Nothing here fabricates a payment.
CREATE SEQUENCE billing_invoice_seq;

CREATE TABLE billing_invoices (
  id               uuid PRIMARY KEY,
  organization_id  uuid NOT NULL REFERENCES organizations (id),
  subscription_id  uuid REFERENCES subscriptions (id),
  number           text NOT NULL UNIQUE,
  status           text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'open', 'paid', 'void')),
  currency         char(3) NOT NULL REFERENCES currencies (code),
  amount_total     numeric(18, 3) NOT NULL CHECK (amount_total >= 0),
  period_start     timestamptz,
  period_end       timestamptz,
  issued_at        timestamptz,
  due_at           timestamptz,
  description      text,
  void_reason      text,
  created_by       uuid NOT NULL REFERENCES platform_admins (id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX billing_invoices_org ON billing_invoices (organization_id, created_at DESC);

CREATE TABLE billing_payments (
  id                  uuid PRIMARY KEY,
  organization_id     uuid NOT NULL REFERENCES organizations (id),
  invoice_id          uuid NOT NULL REFERENCES billing_invoices (id),
  kind                text NOT NULL CHECK (kind IN ('payment', 'refund')),
  amount              numeric(18, 3) NOT NULL CHECK (amount > 0),
  currency            char(3) NOT NULL REFERENCES currencies (code),
  method              text NOT NULL CHECK (method IN ('bank_transfer', 'cash', 'cheque', 'other', 'provider')),
  reference           text,
  provider            text,
  provider_reference  text,
  received_at         timestamptz NOT NULL DEFAULT now(),
  note                text,
  recorded_by         uuid NOT NULL REFERENCES platform_admins (id),
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX billing_payments_invoice ON billing_payments (invoice_id);
CREATE INDEX billing_payments_org ON billing_payments (organization_id, received_at DESC);

CREATE TABLE billing_adjustments (
  id               uuid PRIMARY KEY,
  organization_id  uuid NOT NULL REFERENCES organizations (id),
  invoice_id       uuid NOT NULL REFERENCES billing_invoices (id),
  amount           numeric(18, 3) NOT NULL CHECK (amount <> 0),   -- signed: credit < 0, debit > 0
  currency         char(3) NOT NULL REFERENCES currencies (code),
  reason           text NOT NULL CHECK (length(reason) >= 10),
  created_by       uuid NOT NULL REFERENCES platform_admins (id),
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX billing_adjustments_invoice ON billing_adjustments (invoice_id);

CREATE TRIGGER billing_payments_immutable BEFORE UPDATE OR DELETE ON billing_payments
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();
CREATE TRIGGER billing_adjustments_immutable BEFORE UPDATE OR DELETE ON billing_adjustments
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();

-- ------------------------------------------------------------------ support access workflow
ALTER TABLE support_access_grants
  ADD COLUMN requester_name    text NOT NULL DEFAULT '',
  ADD COLUMN requested_hours   integer NOT NULL DEFAULT 24 CHECK (requested_hours BETWEEN 1 AND 720),
  ADD COLUMN approved_level    text CHECK (approved_level IN ('metadata', 'read')),
  ADD COLUMN decided_at        timestamptz,
  ADD COLUMN decision_note     text,
  ADD COLUMN revoked_reason    text,
  ADD COLUMN revoked_by_type   text CHECK (revoked_by_type IN ('platform_admin', 'customer', 'system'));
CREATE INDEX support_access_grants_org ON support_access_grants (organization_id, created_at DESC);
CREATE INDEX support_access_grants_admin ON support_access_grants (platform_admin_id, status);

-- ------------------------------------------------------------------ platform audit: outcome and reason
ALTER TABLE platform_audit_events
  ADD COLUMN outcome text NOT NULL DEFAULT 'success' CHECK (outcome IN ('success', 'failure', 'denied')),
  ADD COLUMN reason  text;
CREATE INDEX platform_audit_events_actor ON platform_audit_events (actor_id, created_at DESC);
CREATE INDEX platform_audit_events_org ON platform_audit_events (target_organization, created_at DESC);
CREATE INDEX platform_audit_events_action ON platform_audit_events (action, created_at DESC);

-- ------------------------------------------------------------------ invitations created by Artify
ALTER TABLE invitations ALTER COLUMN invited_by DROP NOT NULL;
ALTER TABLE invitations ADD COLUMN invited_by_platform_admin uuid REFERENCES platform_admins (id);
ALTER TABLE invitations ADD CONSTRAINT invitations_inviter CHECK (invited_by IS NOT NULL OR invited_by_platform_admin IS NOT NULL);

-- ------------------------------------------------------------------ RLS on the new tables
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['platform_admin_invitations', 'organization_status_events', 'subscription_events',
                           'billing_invoices', 'billing_payments', 'billing_adjustments'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

-- ------------------------------------------------------------------ grants: platform realm
-- platform_admins: the password hash is never readable by the platform role (column-level grants).
REVOKE SELECT, UPDATE ON platform_admins FROM artify_platform;
GRANT SELECT (id, email, display_name, role_key, status, created_at, updated_at, invited_by) ON platform_admins TO artify_platform;
GRANT UPDATE (display_name, role_key, status, updated_at) ON platform_admins TO artify_platform;
DROP POLICY platform_admin_self ON platform_admins;
CREATE POLICY platform_admins_select ON platform_admins FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_admins_update ON platform_admins FOR UPDATE TO artify_platform USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE (revoked_at) ON platform_admin_invitations TO artify_platform;
CREATE POLICY platform_all ON platform_admin_invitations FOR ALL TO artify_platform USING (true) WITH CHECK (true);

-- Disabling an administrator revokes that administrator's sessions. An UPDATE ... WHERE also needs the rows to
-- be visible through a SELECT policy, so both are granted - for PLATFORM sessions only (tenant sessions have
-- platform_admin_id NULL and stay invisible to this role).
CREATE POLICY platform_sessions_read ON sessions FOR SELECT TO artify_platform
  USING (platform_admin_id IS NOT NULL);
CREATE POLICY platform_sessions_revoke ON sessions FOR UPDATE TO artify_platform
  USING (platform_admin_id IS NOT NULL) WITH CHECK (platform_admin_id IS NOT NULL);

GRANT UPDATE (display_name, legal_name, status, updated_at, row_version) ON organizations TO artify_platform;
CREATE POLICY platform_update ON organizations FOR UPDATE TO artify_platform USING (true) WITH CHECK (true);

GRANT SELECT, INSERT ON organization_status_events, subscription_events TO artify_platform;
CREATE POLICY platform_select ON organization_status_events FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_insert ON organization_status_events FOR INSERT TO artify_platform WITH CHECK (true);
CREATE POLICY platform_select ON subscription_events FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_insert ON subscription_events FOR INSERT TO artify_platform WITH CHECK (true);

GRANT UPDATE (plan_version_id, status, current_period_start, current_period_end, trial_ends_at,
              cancel_at_period_end, cancelled_at, updated_at) ON subscriptions TO artify_platform;
CREATE POLICY platform_update ON subscriptions FOR UPDATE TO artify_platform USING (true) WITH CHECK (true);

GRANT INSERT, UPDATE (expires_at) ON entitlement_overrides TO artify_platform;
CREATE POLICY platform_write ON entitlement_overrides FOR INSERT TO artify_platform WITH CHECK (true);
CREATE POLICY platform_expire ON entitlement_overrides FOR UPDATE TO artify_platform USING (true) WITH CHECK (true);

GRANT SELECT, INSERT ON billing_payments, billing_adjustments TO artify_platform;
GRANT SELECT, INSERT, UPDATE (status, issued_at, due_at, description, void_reason, updated_at) ON billing_invoices TO artify_platform;
GRANT USAGE ON SEQUENCE billing_invoice_seq TO artify_platform;
CREATE POLICY platform_all ON billing_invoices FOR ALL TO artify_platform USING (true) WITH CHECK (true);
CREATE POLICY platform_select ON billing_payments FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_insert ON billing_payments FOR INSERT TO artify_platform WITH CHECK (true);
CREATE POLICY platform_select ON billing_adjustments FOR SELECT TO artify_platform USING (true);
CREATE POLICY platform_insert ON billing_adjustments FOR INSERT TO artify_platform WITH CHECK (true);

-- Support requests: an administrator can only create requests in their own name and can only cancel/revoke.
GRANT INSERT, UPDATE (status, revoked_at, revoked_reason, revoked_by_type) ON support_access_grants TO artify_platform;
CREATE POLICY platform_request ON support_access_grants FOR INSERT TO artify_platform
  WITH CHECK (platform_admin_id = app.user_id() AND status = 'requested' AND approved_by IS NULL);
CREATE POLICY platform_revoke ON support_access_grants FOR UPDATE TO artify_platform
  USING (status IN ('requested', 'active')) WITH CHECK (status = 'revoked');

-- ------------------------------------------------------------------ grant-scoped access (TS-ISO-07)
-- The only way the platform realm reads tenant rows: an ACTIVE, customer-approved, unexpired grant held by
-- the calling administrator. Evaluated once per query (InitPlan, ADR-024).
CREATE FUNCTION app.platform_granted_org_ids() RETURNS uuid[] LANGUAGE sql STABLE AS $$
  SELECT COALESCE(array_agg(DISTINCT g.organization_id), '{}'::uuid[]) FROM support_access_grants g
   WHERE g.platform_admin_id = app.user_id() AND g.status = 'active'
     AND g.starts_at <= now() AND g.expires_at > now()
$$;
REVOKE ALL ON FUNCTION app.platform_granted_org_ids() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.platform_granted_org_ids() TO artify_platform;

GRANT SELECT ON organization_memberships TO artify_platform;
GRANT SELECT (id, email, display_name, status, created_at) ON users TO artify_platform;
GRANT SELECT, INSERT ON audit_events TO artify_platform;
CREATE POLICY platform_granted ON organization_memberships FOR SELECT TO artify_platform
  USING (organization_id = ANY (COALESCE((SELECT app.platform_granted_org_ids()), '{}'::uuid[])));
CREATE POLICY platform_granted ON users FOR SELECT TO artify_platform
  USING (EXISTS (SELECT 1 FROM organization_memberships m WHERE m.user_id = users.id));
CREATE POLICY platform_granted_audit ON audit_events FOR SELECT TO artify_platform
  USING (organization_id = ANY (COALESCE((SELECT app.platform_granted_org_ids()), '{}'::uuid[])));
-- Every grant-based read is mirrored into the customer's own audit log.
CREATE POLICY platform_support_audit ON audit_events FOR INSERT TO artify_platform
  WITH CHECK (actor_type = 'platform_admin' AND support_grant_id IS NOT NULL AND actor_id = app.user_id()
              AND organization_id = ANY (COALESCE((SELECT app.platform_granted_org_ids()), '{}'::uuid[])));

-- ------------------------------------------------------------------ grants: tenant side (customer authorization)
GRANT SELECT, UPDATE (status, approved_by, approved_level, starts_at, expires_at, decided_at, decision_note,
                      revoked_at, revoked_reason, revoked_by_type) ON support_access_grants TO artify_app;
CREATE POLICY tenant_select ON support_access_grants FOR SELECT TO artify_app USING (organization_id = app.org_id());
CREATE POLICY tenant_decide ON support_access_grants FOR UPDATE TO artify_app
  USING (organization_id = app.org_id() AND status IN ('requested', 'active'))
  WITH CHECK (organization_id = app.org_id());

-- ------------------------------------------------------------------ definer: aggregate views and narrow functions
GRANT SELECT ON organizations, accountant_assignments, outbox_events, subscriptions TO artify_definer;
CREATE POLICY definer_read ON organizations            FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON accountant_assignments   FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON outbox_events            FOR SELECT TO artify_definer USING (true);
CREATE POLICY definer_read ON subscriptions            FOR SELECT TO artify_definer USING (true);

GRANT INSERT ON organizations, invitations, subscription_events, platform_admins TO artify_definer;
GRANT SELECT, UPDATE ON platform_admin_invitations TO artify_definer;
CREATE POLICY definer_insert ON organizations           FOR INSERT TO artify_definer WITH CHECK (true);
CREATE POLICY definer_insert ON invitations             FOR INSERT TO artify_definer WITH CHECK (true);
CREATE POLICY definer_insert ON subscription_events     FOR INSERT TO artify_definer WITH CHECK (true);
CREATE POLICY definer_insert ON platform_admins         FOR INSERT TO artify_definer WITH CHECK (true);
CREATE POLICY definer_all ON platform_admin_invitations FOR ALL TO artify_definer USING (true) WITH CHECK (true);

ALTER FUNCTION app.guard_pinned_plan_version() OWNER TO artify_definer;

-- Views run with the privileges of their owner (artify_definer), so the platform realm sees only these
-- aggregates. They contain no document, ledger or tax data.
CREATE VIEW platform_org_overview AS
SELECT o.id, o.type, o.legal_name, o.display_name, o.slug, o.country, o.default_currency, o.locale, o.status,
       o.created_at, o.updated_at,
       ow.email::text AS owner_email, ow.display_name AS owner_name,
       (SELECT count(DISTINCT x.u) FROM (
          SELECT user_id AS u FROM organization_memberships WHERE organization_id = o.id AND status = 'active'
          UNION
          SELECT user_id FROM company_memberships WHERE organization_id = o.id AND status = 'active') x) AS user_count,
       (SELECT count(*) FROM organization_memberships m JOIN roles r ON r.id = m.role_id
         WHERE m.organization_id = o.id AND m.status = 'active' AND r.key IN ('accountant', 'staff')) AS accountant_count,
       (SELECT count(*) FROM companies c WHERE c.organization_id = o.id AND c.status <> 'archived') AS company_count,
       (SELECT count(*) FROM companies c WHERE c.organization_id = o.id) AS company_total
  FROM organizations o
  LEFT JOIN LATERAL (
    SELECT u.email, u.display_name FROM organization_memberships m
      JOIN roles r ON r.id = m.role_id JOIN users u ON u.id = m.user_id
     WHERE m.organization_id = o.id AND r.key = 'firm_owner' AND m.status = 'active'
     ORDER BY m.created_at LIMIT 1) ow ON true;

CREATE VIEW platform_company_overview AS
SELECT c.id, c.organization_id, o.display_name AS organization_name, o.type AS organization_type,
       c.name, c.code, c.country, c.base_currency, c.status, c.created_at,
       (SELECT count(*) FROM accountant_assignments a WHERE a.company_id = c.id) AS accountant_count,
       (SELECT count(*) FROM company_memberships cm WHERE cm.company_id = c.id AND cm.status = 'active') AS user_count
  FROM companies c JOIN organizations o ON o.id = c.organization_id;

CREATE VIEW platform_user_totals AS
SELECT (SELECT count(*) FROM users) AS total_users,
       (SELECT count(*) FROM users WHERE status = 'active') AS active_users;

CREATE VIEW platform_outbox_overview AS
SELECT state, count(*) AS n, min(created_at) AS oldest FROM outbox_events GROUP BY state;

ALTER VIEW platform_org_overview OWNER TO artify_definer;
ALTER VIEW platform_company_overview OWNER TO artify_definer;
ALTER VIEW platform_user_totals OWNER TO artify_definer;
ALTER VIEW platform_outbox_overview OWNER TO artify_definer;
GRANT SELECT ON platform_org_overview, platform_company_overview, platform_user_totals, platform_outbox_overview
  TO artify_platform;

-- Artify-provisioned organization: organization + trial subscription + pending owner invitation, atomically.
-- Callable only by an active platform_owner / platform_admin (checked here as defense in depth).
CREATE FUNCTION app.platform_provision_organization(
  p_org uuid, p_type text, p_legal text, p_display text, p_slug text, p_country text, p_currency text,
  p_locale text, p_plan_version uuid, p_trial_days integer, p_owner_email text, p_inv uuid, p_sub uuid,
  p_token bytea, p_expires timestamptz) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_admin uuid := app.user_id();
BEGIN
  IF NOT EXISTS (SELECT 1 FROM platform_admins WHERE id = v_admin AND status = 'active'
                  AND role_key IN ('platform_owner', 'platform_admin')) THEN
    RAISE EXCEPTION 'not an authorized platform administrator' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO organizations (id, type, legal_name, display_name, slug, country, default_currency, locale)
  VALUES (p_org, p_type, p_legal, p_display, p_slug, p_country, p_currency, p_locale);
  INSERT INTO subscriptions (id, organization_id, plan_version_id, status, current_period_start, trial_ends_at)
  VALUES (p_sub, p_org, p_plan_version, 'trialing', now(), now() + make_interval(days => COALESCE(p_trial_days, 30)));
  INSERT INTO subscription_events (id, subscription_id, organization_id, event_type, to_status, to_plan_version_id,
                                   actor_type, actor_id, reason)
  VALUES (gen_random_uuid(), p_sub, p_org, 'created', 'trialing', p_plan_version, 'platform_admin', v_admin,
          'organization provisioned by Artify');
  INSERT INTO invitations (id, organization_id, email, org_role_id, token_hash, invited_by_platform_admin, expires_at)
  SELECT p_inv, p_org, p_owner_email, r.id, p_token, v_admin, p_expires FROM roles r
   WHERE r.key = 'firm_owner' AND r.organization_id IS NULL;
  RETURN p_org;
END $$;
ALTER FUNCTION app.platform_provision_organization(uuid, text, text, text, text, text, text, text, uuid, integer, text, uuid, uuid, bytea, timestamptz) OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.platform_provision_organization(uuid, text, text, text, text, text, text, text, uuid, integer, text, uuid, uuid, bytea, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.platform_provision_organization(uuid, text, text, text, text, text, text, text, uuid, integer, text, uuid, uuid, bytea, timestamptz) TO artify_platform;

-- Accepting a platform-administrator invitation (pre-authentication, token is the credential).
CREATE FUNCTION app.platform_accept_invitation(p_hash bytea, p_admin uuid, p_name text, p_password_hash text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE inv platform_admin_invitations%ROWTYPE;
BEGIN
  SELECT * INTO inv FROM platform_admin_invitations WHERE token_hash = p_hash FOR UPDATE;
  IF NOT FOUND OR inv.accepted_at IS NOT NULL OR inv.revoked_at IS NOT NULL OR inv.expires_at <= now() THEN
    RETURN NULL;
  END IF;
  IF EXISTS (SELECT 1 FROM platform_admins WHERE email = inv.email) THEN
    RETURN NULL;
  END IF;
  INSERT INTO platform_admins (id, email, display_name, role_key, password_hash, invited_by)
  VALUES (p_admin, inv.email, p_name, inv.role_key, p_password_hash, inv.invited_by);
  UPDATE platform_admin_invitations SET accepted_at = now() WHERE id = inv.id;
  RETURN inv.role_key;
END $$;
ALTER FUNCTION app.platform_accept_invitation(bytea, uuid, text, text) OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.platform_accept_invitation(bytea, uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.platform_accept_invitation(bytea, uuid, text, text) TO artify_platform;

-- Signup trial subscriptions now leave a history record too.
CREATE OR REPLACE FUNCTION app.create_trial_subscription(p_id uuid, p_org uuid) RETURNS uuid
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
  INSERT INTO subscription_events (id, subscription_id, organization_id, event_type, to_status, to_plan_version_id,
                                   actor_type, reason)
  VALUES (gen_random_uuid(), p_id, p_org, 'created', 'trialing', v_version, 'system', 'self-service sign-up');
  RETURN p_id;
END $$;
