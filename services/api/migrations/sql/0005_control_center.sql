-- Phase 3: Firm Control Center (customer-facing management of an organization and its client companies).
-- Extends Phase 1 tables; no second user/organization/company model. Every new company-owned table carries the
-- composite (organization_id, company_id) FK and forced RLS using the ADR-024 company-scope pattern.

-- ------------------------------------------------------------------ organization settings
ALTER TABLE organizations
  ADD COLUMN contact_email            text,
  ADD COLUMN contact_phone            text,
  ADD COLUMN address_line             text,
  ADD COLUMN city                     text,
  ADD COLUMN timezone                 text NOT NULL DEFAULT 'UTC',
  ADD COLUMN notification_preferences jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ------------------------------------------------------------------ company profile and accounting settings
-- Each company owns its own values: nothing here is inherited from, or written back to, the organization or
-- another company. Jurisdiction-specific tax rules are NOT encoded: `tax_jurisdiction` is free configuration that
-- later phases interpret through configurable rule sets.
ALTER TABLE companies
  ADD COLUMN legal_name                  text,
  ADD COLUMN registration_number         text,
  ADD COLUMN company_type                text,
  ADD COLUMN industry                    text,
  ADD COLUMN city                        text,
  ADD COLUMN address_line                text,
  ADD COLUMN contact_email               text,
  ADD COLUMN contact_phone               text,
  ADD COLUMN financial_year_start_month  smallint NOT NULL DEFAULT 1 CHECK (financial_year_start_month BETWEEN 1 AND 12),
  ADD COLUMN financial_year_start_day    smallint NOT NULL DEFAULT 1 CHECK (financial_year_start_day BETWEEN 1 AND 31),
  ADD COLUMN accounting_basis            text CHECK (accounting_basis IN ('accrual', 'cash')),
  ADD COLUMN vat_registered              boolean NOT NULL DEFAULT false,
  ADD COLUMN vat_number                  text,
  ADD COLUMN tax_jurisdiction            text,
  ADD CONSTRAINT companies_vat_number_requires_registration CHECK (vat_number IS NULL OR vat_registered);

-- ------------------------------------------------------------------ company configuration ownership (Phase 4 integration point)
-- One row per company. Phase 4 attaches workspace structures, categories, folders, custom fields, templates,
-- workflows and local folder mappings to the COMPANY (never to the organization or a global default); this row
-- is the owner record those later tables reference and version. In Phase 3 it only records that nothing has
-- been configured. No default section list, folder tree or category exists anywhere.
CREATE TABLE company_configurations (
  company_id        uuid PRIMARY KEY,
  organization_id   uuid NOT NULL,
  workspace_state   text NOT NULL DEFAULT 'not_configured' CHECK (workspace_state IN ('not_configured', 'configured')),
  config_version    integer NOT NULL DEFAULT 1 CHECK (config_version >= 1),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id)
);
INSERT INTO company_configurations (company_id, organization_id) SELECT id, organization_id FROM companies;
ALTER TABLE company_configurations ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_configurations FORCE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON company_configurations TO artify_app;
CREATE POLICY cc_select ON company_configurations FOR SELECT TO artify_app
  USING (organization_id = app.org_id() AND (app.company_scope() = 'all' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), '{}'::uuid[]))));
CREATE POLICY cc_insert ON company_configurations FOR INSERT TO artify_app
  WITH CHECK (organization_id = app.org_id() AND app.company_scope() = 'all');
CREATE POLICY cc_update ON company_configurations FOR UPDATE TO artify_app
  USING (organization_id = app.org_id() AND (app.company_scope() = 'all' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), '{}'::uuid[]))))
  WITH CHECK (organization_id = app.org_id() AND (app.company_scope() = 'all' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), '{}'::uuid[]))));

-- ------------------------------------------------------------------ assignments: responsibility and window
ALTER TABLE accountant_assignments
  ADD COLUMN responsibility text,
  ADD COLUMN starts_on  date NOT NULL DEFAULT current_date,
  ADD COLUMN ends_on    date,
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
  ADD CONSTRAINT accountant_assignments_window CHECK (ends_on IS NULL OR ends_on >= starts_on);

-- Who can be assigned to specific companies: every organization role that does NOT reach all companies.
-- (permissions.py ASSIGNABLE_ROLES; owner, administrator and practice manager already reach every company.)
CREATE OR REPLACE FUNCTION app.check_assignment_member() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM organization_memberships m JOIN roles r ON r.id = m.role_id
    WHERE m.organization_id = NEW.organization_id AND m.user_id = NEW.user_id AND m.status = 'active'
      AND r.key IN ('accountant', 'assistant_accountant', 'document_operator', 'read_only', 'staff')
  ) THEN
    RAISE EXCEPTION 'user % is not an active, assignable member of the organization', NEW.user_id
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;

-- Professional staff counted by limit.accountants (permissions.py ACCOUNTANT_ROLES).
CREATE OR REPLACE FUNCTION app.measure_entitlement(p_org uuid, p_key text) RETURNS bigint
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
     WHERE m.organization_id = p_org AND m.status = 'active'
       AND r.key IN ('accountant', 'assistant_accountant', 'staff');
  ELSIF p_key = 'limit.devices' THEN
    SELECT count(*) INTO v FROM devices WHERE organization_id = p_org AND status = 'active';
  ELSE
    RAISE EXCEPTION 'entitlement % is not measurable yet', p_key USING ERRCODE = 'feature_not_supported';
  END IF;
  RETURN v;
END $$;

CREATE OR REPLACE VIEW platform_org_overview AS
SELECT o.id, o.type, o.legal_name, o.display_name, o.slug, o.country, o.default_currency, o.locale, o.status,
       o.created_at, o.updated_at,
       ow.email::text AS owner_email, ow.display_name AS owner_name,
       (SELECT count(DISTINCT x.u) FROM (
          SELECT user_id AS u FROM organization_memberships WHERE organization_id = o.id AND status = 'active'
          UNION
          SELECT user_id FROM company_memberships WHERE organization_id = o.id AND status = 'active') x) AS user_count,
       (SELECT count(*) FROM organization_memberships m JOIN roles r ON r.id = m.role_id
         WHERE m.organization_id = o.id AND m.status = 'active'
           AND r.key IN ('accountant', 'assistant_accountant', 'staff')) AS accountant_count,
       (SELECT count(*) FROM companies c WHERE c.organization_id = o.id AND c.status <> 'archived') AS company_count,
       (SELECT count(*) FROM companies c WHERE c.organization_id = o.id) AS company_total
  FROM organizations o
  LEFT JOIN LATERAL (
    SELECT u.email, u.display_name FROM organization_memberships m
      JOIN roles r ON r.id = m.role_id JOIN users u ON u.id = m.user_id
     WHERE m.organization_id = o.id AND r.key = 'firm_owner' AND m.status = 'active'
     ORDER BY m.created_at LIMIT 1) ow ON true;

-- ------------------------------------------------------------------ professional profile (separate from the authorization role)
-- A person's job title or professional details never grant or limit access; the organization role does.
CREATE TABLE member_profiles (
  organization_id  uuid NOT NULL REFERENCES organizations (id),
  user_id          uuid NOT NULL REFERENCES users (id),
  job_title        text,
  phone            text,
  license_number   text,
  updated_by       uuid REFERENCES users (id),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, user_id)
);
ALTER TABLE member_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE member_profiles FORCE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON member_profiles TO artify_app;
CREATE POLICY mp_select ON member_profiles FOR SELECT TO artify_app USING (organization_id = app.org_id());
CREATE POLICY mp_insert ON member_profiles FOR INSERT TO artify_app WITH CHECK (organization_id = app.org_id());
CREATE POLICY mp_update ON member_profiles FOR UPDATE TO artify_app
  USING (organization_id = app.org_id()) WITH CHECK (organization_id = app.org_id());

-- ------------------------------------------------------------------ invitations: company access at invite time, decline, resend
ALTER TABLE invitations
  ADD COLUMN company_ids  uuid[] NOT NULL DEFAULT '{}'::uuid[],
  ADD COLUMN declined_at  timestamptz,
  ADD COLUMN resent_at    timestamptz,
  ADD COLUMN resent_count integer NOT NULL DEFAULT 0;

DROP FUNCTION app.auth_invitation_by_hash(bytea);
CREATE FUNCTION app.auth_invitation_by_hash(p_hash bytea)
RETURNS TABLE (id uuid, organization_id uuid, email citext, org_role_id uuid, company_id uuid,
               company_role_id uuid, expires_at timestamptz, accepted_at timestamptz, revoked_at timestamptz,
               company_ids uuid[], declined_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT i.id, i.organization_id, i.email, i.org_role_id, i.company_id, i.company_role_id,
         i.expires_at, i.accepted_at, i.revoked_at, i.company_ids, i.declined_at
  FROM invitations i WHERE i.token_hash = p_hash
$$;
ALTER FUNCTION app.auth_invitation_by_hash(bytea) OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.auth_invitation_by_hash(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.auth_invitation_by_hash(bytea) TO artify_app;

-- Declining is a pre-authentication action (the token is the credential); it can only mark a pending invitation.
CREATE FUNCTION app.decline_invitation(p_hash bytea) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  WITH u AS (
    UPDATE invitations SET declined_at = now(), revoked_at = now()
     WHERE token_hash = p_hash AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()
     RETURNING 1)
  SELECT EXISTS (SELECT 1 FROM u)
$$;
ALTER FUNCTION app.decline_invitation(bytea) OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.decline_invitation(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.decline_invitation(bytea) TO artify_app;
GRANT UPDATE ON invitations TO artify_definer;
CREATE POLICY definer_update ON invitations FOR UPDATE TO artify_definer USING (true) WITH CHECK (true);

-- ------------------------------------------------------------------ Phase 2 correction: usage counters for provisioned organizations
-- An organization created by Artify (platform_provision_organization) had no usage counters until its first
-- mutation, so enforceable limits showed as "not metered". The organization is empty at provisioning, so zero is
-- the true value of each count.
GRANT INSERT ON usage_counters TO artify_definer;
CREATE POLICY definer_insert ON usage_counters FOR INSERT TO artify_definer WITH CHECK (true);

CREATE OR REPLACE FUNCTION app.platform_provision_organization(
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
  INSERT INTO usage_counters (id, organization_id, metric, value)
  SELECT gen_random_uuid(), p_org, k, 0
    FROM unnest(ARRAY['limit.companies', 'limit.users', 'limit.accountants', 'limit.devices']) AS k;
  RETURN p_org;
END $$;
