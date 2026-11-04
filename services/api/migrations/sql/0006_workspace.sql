-- Phase 4: Workspace structure & customization engine.
-- Every company-owned table carries organization_id + company_id with the composite FK to companies, forced RLS and the
-- ADR-024 company-scope policy. Nothing here seeds a default structure: companies start empty.

-- ------------------------------------------------------------------ reference data (Artify-controlled, no tenant data)
CREATE TABLE document_kinds (
  key               text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,47}$'),
  name              text NOT NULL,
  description       text NOT NULL DEFAULT '',
  accounting_domain text NOT NULL,
  processing_type   text NOT NULL,
  creates_financial_records boolean NOT NULL DEFAULT false,
  is_active         boolean NOT NULL DEFAULT true,
  sort_order        integer NOT NULL DEFAULT 0
);
GRANT SELECT ON document_kinds TO artify_app, artify_platform;

-- ------------------------------------------------------------------ company configuration: access mode + provenance
ALTER TABLE company_configurations
  ADD COLUMN workspace_access_mode text NOT NULL DEFAULT 'open' CHECK (workspace_access_mode IN ('open', 'restricted')),
  ADD COLUMN applied_template_id uuid,
  ADD COLUMN applied_template_version integer;

CREATE FUNCTION app.is_unique_uuid_array(a uuid[]) RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT cardinality(a) = (SELECT count(DISTINCT x) FROM unnest(a) AS x) $$;

-- ------------------------------------------------------------------ workspace nodes (sections are roots, folders nest below)
CREATE TABLE workspace_nodes (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  parent_id       uuid,
  node_type       text NOT NULL CHECK (node_type IN ('section', 'folder')),
  key             text NOT NULL CHECK (key ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
  name            text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120
                                       AND name !~ '[/\\:*?"<>|[:cntrl:]]' AND btrim(name) NOT IN ('.', '..')),
  display_name    text CHECK (display_name IS NULL OR length(btrim(display_name)) BETWEEN 1 AND 160),
  description     text NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
  position        integer NOT NULL DEFAULT 0,
  depth           smallint NOT NULL CHECK (depth BETWEEN 1 AND 32),
  path_ids        uuid[] NOT NULL,
  config          jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(config) = 'object'),
  is_active       boolean NOT NULL DEFAULT true,
  archived_at     timestamptz,
  archived_by     uuid REFERENCES users (id),
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_by      uuid REFERENCES users (id),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  row_version     integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (company_id, key),
  FOREIGN KEY (organization_id, company_id, parent_id) REFERENCES workspace_nodes (organization_id, company_id, id),
  CONSTRAINT workspace_nodes_type_parent CHECK ((node_type = 'section') = (parent_id IS NULL)),
  CONSTRAINT workspace_nodes_no_self_parent CHECK (parent_id IS DISTINCT FROM id),
  CONSTRAINT workspace_nodes_path CHECK (
    depth = cardinality(path_ids) AND path_ids[cardinality(path_ids)] = id
    AND (parent_id IS NULL OR (cardinality(path_ids) > 1 AND path_ids[cardinality(path_ids) - 1] = parent_id))
    AND app.is_unique_uuid_array(path_ids))
);
-- sibling names are unique (case-insensitive) among non-archived siblings: local Windows folders stay unambiguous
CREATE UNIQUE INDEX workspace_nodes_sibling_name ON workspace_nodes
  (company_id, COALESCE(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name)) WHERE archived_at IS NULL;
CREATE INDEX workspace_nodes_parent ON workspace_nodes (company_id, parent_id, position);
CREATE INDEX workspace_nodes_path ON workspace_nodes USING gin (path_ids);

-- ------------------------------------------------------------------ document categories (organization layer) -> document kinds (accounting layer)
CREATE TABLE document_categories (
  id                  uuid PRIMARY KEY,
  organization_id     uuid NOT NULL,
  company_id          uuid NOT NULL,
  key                 text NOT NULL CHECK (key ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
  name                text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  description         text NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
  document_kind       text NOT NULL REFERENCES document_kinds (key),
  accounting_domain   text NOT NULL,
  processing_type     text NOT NULL,
  allowed_file_types  text[] NOT NULL DEFAULT '{}' CHECK (cardinality(allowed_file_types) <= 30),
  processing_settings jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(processing_settings) = 'object'),
  node_id             uuid,
  is_active           boolean NOT NULL DEFAULT true,
  archived_at         timestamptz,
  created_by          uuid REFERENCES users (id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_by          uuid REFERENCES users (id),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (company_id, key),
  FOREIGN KEY (organization_id, company_id, node_id) REFERENCES workspace_nodes (organization_id, company_id, id)
);
CREATE UNIQUE INDEX document_categories_name ON document_categories (company_id, lower(name)) WHERE archived_at IS NULL;

-- ------------------------------------------------------------------ custom metadata field definitions (values arrive with documents in Phase 6)
CREATE TABLE custom_field_definitions (
  id               uuid PRIMARY KEY,
  organization_id  uuid NOT NULL,
  company_id       uuid NOT NULL,
  key              text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{0,47}$'),
  label            text NOT NULL CHECK (length(btrim(label)) BETWEEN 1 AND 120),
  description      text NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
  data_type        text NOT NULL CHECK (data_type IN ('short_text', 'long_text', 'number', 'decimal', 'currency',
                                                      'date', 'dropdown', 'multi_select', 'boolean', 'reference')),
  required         boolean NOT NULL DEFAULT false,
  default_value    jsonb,
  validation       jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(validation) = 'object'),
  reference_target text CHECK (reference_target IS NULL OR reference_target IN ('workspace_node', 'document_category')),
  display_order    integer NOT NULL DEFAULT 0,
  is_active        boolean NOT NULL DEFAULT true,
  archived_at      timestamptz,
  created_by       uuid REFERENCES users (id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_by       uuid REFERENCES users (id),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (company_id, key),
  CONSTRAINT custom_field_reference_target CHECK ((data_type = 'reference') = (reference_target IS NOT NULL))
);
CREATE UNIQUE INDEX custom_field_label ON custom_field_definitions (company_id, lower(label)) WHERE archived_at IS NULL;

CREATE TABLE custom_field_options (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  field_id        uuid NOT NULL,
  value           text NOT NULL CHECK (length(btrim(value)) BETWEEN 1 AND 100),
  label           text NOT NULL CHECK (length(btrim(label)) BETWEEN 1 AND 160),
  position        integer NOT NULL DEFAULT 0,
  archived_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, field_id) REFERENCES custom_field_definitions (organization_id, company_id, id),
  UNIQUE (field_id, value)
);

CREATE TABLE custom_field_applicability (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  field_id        uuid NOT NULL,
  category_id     uuid,
  node_id         uuid,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, field_id) REFERENCES custom_field_definitions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, category_id) REFERENCES document_categories (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, node_id) REFERENCES workspace_nodes (organization_id, company_id, id),
  CONSTRAINT custom_field_applicability_one_target CHECK ((category_id IS NULL) <> (node_id IS NULL)),
  UNIQUE NULLS NOT DISTINCT (field_id, category_id, node_id)
);

-- ------------------------------------------------------------------ naming rules and filing rules (configuration only)
CREATE TABLE naming_rules (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  name            text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  description     text NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
  pattern         text NOT NULL CHECK (length(pattern) BETWEEN 1 AND 300),
  category_id     uuid,
  is_active       boolean NOT NULL DEFAULT true,
  archived_at     timestamptz,
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_by      uuid REFERENCES users (id),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, category_id) REFERENCES document_categories (organization_id, company_id, id)
);
-- at most one live rule per category (and one company-wide default)
CREATE UNIQUE INDEX naming_rules_one_per_category ON naming_rules
  (company_id, COALESCE(category_id, '00000000-0000-0000-0000-000000000000'::uuid)) WHERE archived_at IS NULL AND is_active;

CREATE TABLE filing_rules (
  id                 uuid PRIMARY KEY,
  organization_id    uuid NOT NULL,
  company_id         uuid NOT NULL,
  name               text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  description        text NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
  priority           integer NOT NULL CHECK (priority BETWEEN 1 AND 100000),
  conditions         jsonb NOT NULL CHECK (jsonb_typeof(conditions) = 'array' AND jsonb_array_length(conditions) BETWEEN 1 AND 20),
  target_node_id     uuid NOT NULL,
  target_category_id uuid,
  is_active          boolean NOT NULL DEFAULT true,
  archived_at        timestamptz,
  created_by         uuid REFERENCES users (id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_by         uuid REFERENCES users (id),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, target_node_id) REFERENCES workspace_nodes (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, target_category_id) REFERENCES document_categories (organization_id, company_id, id)
);
CREATE INDEX filing_rules_order ON filing_rules (company_id, priority, created_at, id) WHERE archived_at IS NULL;

-- ------------------------------------------------------------------ workspace permissions (explicit allow/deny grants)
CREATE TABLE workspace_permissions (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  node_id         uuid,   -- NULL = the whole company workspace
  subject_type    text NOT NULL CHECK (subject_type IN ('user', 'role')),
  subject_user_id uuid REFERENCES users (id),
  subject_role    text,
  permission      text NOT NULL CHECK (permission IN ('view', 'upload', 'edit', 'rename', 'move', 'archive', 'delete',
                                                      'configure', 'manage_permissions')),
  effect          text NOT NULL CHECK (effect IN ('allow', 'deny')),
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, node_id) REFERENCES workspace_nodes (organization_id, company_id, id),
  CONSTRAINT workspace_permissions_subject CHECK (
    (subject_type = 'user' AND subject_user_id IS NOT NULL AND subject_role IS NULL)
    OR (subject_type = 'role' AND subject_role IS NOT NULL AND subject_user_id IS NULL)),
  UNIQUE NULLS NOT DISTINCT (company_id, node_id, subject_type, subject_user_id, subject_role, permission)
);
CREATE INDEX workspace_permissions_company ON workspace_permissions (company_id, node_id);

-- ------------------------------------------------------------------ templates (system-owned: organization_id NULL; organization-owned otherwise)
CREATE TABLE workspace_templates (
  id              uuid PRIMARY KEY,
  organization_id uuid REFERENCES organizations (id),
  key             text NOT NULL CHECK (key ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
  name            text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  description     text NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
  source_company_id uuid,
  archived_at     timestamptz,
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (organization_id, key)
);
CREATE TABLE workspace_template_versions (
  id              uuid PRIMARY KEY,
  template_id     uuid NOT NULL REFERENCES workspace_templates (id),
  organization_id uuid REFERENCES organizations (id),
  version         integer NOT NULL CHECK (version >= 1),
  definition      jsonb NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
  changelog       text NOT NULL DEFAULT '' CHECK (length(changelog) <= 1000),
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (template_id, version)
);
ALTER TABLE company_configurations
  ADD FOREIGN KEY (applied_template_id) REFERENCES workspace_templates (id);

-- ------------------------------------------------------------------ immutable revision history
CREATE TABLE workspace_revisions (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  version         integer NOT NULL CHECK (version >= 2),
  action          text NOT NULL,
  changes         jsonb NOT NULL DEFAULT '[]'::jsonb,
  snapshot        jsonb NOT NULL,
  actor_id        uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (company_id, version)
);

-- ------------------------------------------------------------------ row-level security (ADR-024 pattern)
DO $$
DECLARE
  t text;
  scope text := 'organization_id = app.org_id() AND (app.company_scope() = ''all'' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), ''{}''::uuid[])))';
BEGIN
  FOREACH t IN ARRAY ARRAY['workspace_nodes', 'document_categories', 'custom_field_definitions', 'custom_field_options',
                           'custom_field_applicability', 'naming_rules', 'filing_rules', 'workspace_permissions',
                           'workspace_revisions'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO artify_app USING (%s)', t || '_select', t, scope);
    EXECUTE format('CREATE POLICY %I ON %I FOR INSERT TO artify_app WITH CHECK (%s)', t || '_insert', t, scope);
    IF t <> 'workspace_revisions' THEN
      EXECUTE format('CREATE POLICY %I ON %I FOR UPDATE TO artify_app USING (%s) WITH CHECK (%s)', t || '_update', t, scope, scope);
    END IF;
  END LOOP;
  FOREACH t IN ARRAY ARRAY['workspace_permissions', 'custom_field_applicability', 'custom_field_options'] LOOP
    EXECUTE format('CREATE POLICY %I ON %I FOR DELETE TO artify_app USING (%s)', t || '_delete', t, scope);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON workspace_nodes, document_categories, custom_field_definitions, custom_field_options,
  custom_field_applicability, naming_rules, filing_rules, workspace_permissions TO artify_app;
GRANT DELETE ON workspace_nodes, workspace_permissions, custom_field_applicability, custom_field_options TO artify_app;
CREATE POLICY workspace_nodes_delete ON workspace_nodes FOR DELETE TO artify_app USING (
  organization_id = app.org_id() AND (app.company_scope() = 'all' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), '{}'::uuid[]))));
GRANT SELECT, INSERT ON workspace_revisions TO artify_app;

-- templates: system rows (organization_id NULL) are readable by every tenant and writable by none (seed runs as owner)
ALTER TABLE workspace_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_templates FORCE ROW LEVEL SECURITY;
ALTER TABLE workspace_template_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_template_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_templates_select ON workspace_templates FOR SELECT TO artify_app
  USING (organization_id IS NULL OR organization_id = app.org_id());
CREATE POLICY workspace_templates_insert ON workspace_templates FOR INSERT TO artify_app
  WITH CHECK (organization_id = app.org_id());
CREATE POLICY workspace_templates_update ON workspace_templates FOR UPDATE TO artify_app
  USING (organization_id = app.org_id()) WITH CHECK (organization_id = app.org_id());
CREATE POLICY workspace_template_versions_select ON workspace_template_versions FOR SELECT TO artify_app
  USING (organization_id IS NULL OR organization_id = app.org_id());
CREATE POLICY workspace_template_versions_insert ON workspace_template_versions FOR INSERT TO artify_app
  WITH CHECK (organization_id = app.org_id());
GRANT SELECT, INSERT, UPDATE ON workspace_templates TO artify_app;
GRANT SELECT, INSERT ON workspace_template_versions TO artify_app;

-- organization-scope measurement of templates (limit.workspace_templates)
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
  ELSIF p_key = 'limit.workspace_templates' THEN
    SELECT count(*) INTO v FROM workspace_templates WHERE organization_id = p_org AND archived_at IS NULL;
  ELSE
    RAISE EXCEPTION 'entitlement % is not measurable yet', p_key USING ERRCODE = 'feature_not_supported';
  END IF;
  RETURN v;
END $$;

-- the narrow definer function counts an organization's templates; it is bound to app.org_id() inside the function
GRANT SELECT ON workspace_templates TO artify_definer;
CREATE POLICY definer_read ON workspace_templates FOR SELECT TO artify_definer USING (organization_id = app.org_id());
