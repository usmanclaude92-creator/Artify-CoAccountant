-- Phase 9: Excel templates, mappings, exports and reports.
-- Everything is company-owned. Template files and generated workbooks live in the Phase 6 object store (storage_objects);
-- versions, activated mappings and finished exports are immutable. Exports are historical artifacts: never overwritten.

ALTER TABLE storage_objects DROP CONSTRAINT storage_objects_purpose_check;
ALTER TABLE storage_objects ADD CONSTRAINT storage_objects_purpose_check CHECK (purpose IN ('original', 'derived', 'template', 'export'));

CREATE TABLE xl_templates (
  id                 uuid PRIMARY KEY,
  organization_id    uuid NOT NULL,
  company_id         uuid NOT NULL,
  name               text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  description        text CHECK (description IS NULL OR length(description) <= 500),
  category           text NOT NULL CHECK (category ~ '^[a-z][a-z0-9_]{1,59}$'),
  file_kind          text NOT NULL CHECK (file_kind IN ('xlsx', 'xlsm')),
  status             text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'inactive', 'archived')),
  current_version_no integer NOT NULL DEFAULT 1 CHECK (current_version_no >= 1),
  source_template_id uuid,
  created_by         uuid REFERENCES users (id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_by         uuid REFERENCES users (id),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  archived_at        timestamptz,
  row_version        integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, source_template_id) REFERENCES xl_templates (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id)
);
CREATE UNIQUE INDEX xl_templates_name ON xl_templates (company_id, lower(name)) WHERE status <> 'archived';
CREATE INDEX xl_templates_by_company ON xl_templates (company_id, status);

CREATE TABLE xl_template_versions (
  id                uuid PRIMARY KEY,
  organization_id   uuid NOT NULL,
  company_id        uuid NOT NULL,
  template_id       uuid NOT NULL,
  version_no        integer NOT NULL CHECK (version_no >= 1),
  storage_object_id uuid NOT NULL,
  original_filename text NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  file_kind         text NOT NULL CHECK (file_kind IN ('xlsx', 'xlsm')),
  size_bytes        bigint NOT NULL CHECK (size_bytes > 0),
  sha256            text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  analysis          jsonb NOT NULL,
  risk_level        text NOT NULL CHECK (risk_level IN ('none', 'warned', 'unsupported')),
  change_note       text CHECK (change_note IS NULL OR length(change_note) <= 500),
  uploaded_by       uuid REFERENCES users (id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, template_id) REFERENCES xl_templates (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, storage_object_id) REFERENCES storage_objects (organization_id, company_id, id),
  UNIQUE (template_id, version_no),
  UNIQUE (organization_id, company_id, id)
);

CREATE TABLE xl_mappings (
  id               uuid PRIMARY KEY,
  organization_id  uuid NOT NULL,
  company_id       uuid NOT NULL,
  template_id      uuid NOT NULL,
  version_id       uuid NOT NULL,
  mapping_no       integer NOT NULL CHECK (mapping_no >= 1),
  status           text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'superseded')),
  definition       jsonb NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
  validation       jsonb NOT NULL DEFAULT '[]'::jsonb,
  validated_at     timestamptz,
  acknowledged_risks boolean NOT NULL DEFAULT false,
  note             text CHECK (note IS NULL OR length(note) <= 500),
  created_by       uuid REFERENCES users (id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  activated_by     uuid REFERENCES users (id),
  activated_at     timestamptz,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, template_id) REFERENCES xl_templates (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, version_id) REFERENCES xl_template_versions (organization_id, company_id, id),
  UNIQUE (template_id, mapping_no),
  UNIQUE (organization_id, company_id, id),
  CHECK (status = 'draft' OR (activated_by IS NOT NULL AND activated_at IS NOT NULL))
);
CREATE UNIQUE INDEX xl_mappings_one_active ON xl_mappings (template_id) WHERE status = 'active';

CREATE TABLE xl_exports (
  id                 uuid PRIMARY KEY,
  organization_id    uuid NOT NULL,
  company_id         uuid NOT NULL,
  template_id        uuid NOT NULL,
  version_id         uuid NOT NULL,
  mapping_id         uuid NOT NULL,
  idempotency_key    text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
  status             text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed')),
  progress           integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  params             jsonb NOT NULL DEFAULT '{}'::jsonb,
  include_unapproved boolean NOT NULL DEFAULT false,
  source_export_id   uuid,
  reason             text CHECK (reason IS NULL OR length(reason) <= 500),
  repeat_acknowledged boolean NOT NULL DEFAULT false,
  filename           text,
  storage_object_id  uuid,
  sha256             text CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes         bigint,
  row_counts         jsonb NOT NULL DEFAULT '{}'::jsonb,
  validation         jsonb NOT NULL DEFAULT '[]'::jsonb,
  fidelity           jsonb NOT NULL DEFAULT '[]'::jsonb,
  error_code         text,
  error_message      text,
  attempt_count      integer NOT NULL DEFAULT 0,
  created_by         uuid REFERENCES users (id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  started_at         timestamptz,
  finished_at        timestamptz,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, template_id) REFERENCES xl_templates (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, version_id) REFERENCES xl_template_versions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, mapping_id) REFERENCES xl_mappings (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, storage_object_id) REFERENCES storage_objects (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, source_export_id) REFERENCES xl_exports (organization_id, company_id, id),
  UNIQUE (company_id, created_by, idempotency_key),
  UNIQUE (organization_id, company_id, id),
  -- a completed export is a file that was validated and stored
  CHECK (status <> 'completed' OR (storage_object_id IS NOT NULL AND sha256 IS NOT NULL AND filename IS NOT NULL AND finished_at IS NOT NULL))
);
CREATE INDEX xl_exports_by_company ON xl_exports (company_id, created_at DESC);

CREATE TABLE xl_export_rows (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  export_id       uuid NOT NULL,
  transaction_id  uuid,
  journal_id      uuid,
  sheet           text NOT NULL,
  row_no          integer NOT NULL,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, export_id) REFERENCES xl_exports (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, transaction_id) REFERENCES acct_transactions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, journal_id) REFERENCES acct_journals (organization_id, company_id, id)
);
CREATE INDEX xl_export_rows_by_export ON xl_export_rows (export_id);
CREATE INDEX xl_export_rows_by_transaction ON xl_export_rows (company_id, transaction_id) WHERE transaction_id IS NOT NULL;

CREATE TABLE xl_reports (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  name            text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  description     text CHECK (description IS NULL OR length(description) <= 500),
  category        text NOT NULL CHECK (category ~ '^[a-z][a-z0-9_]{1,59}$'),
  dataset         text NOT NULL,
  definition      jsonb NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
  template_id     uuid,
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_by      uuid REFERENCES users (id),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  row_version     integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, template_id) REFERENCES xl_templates (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id)
);
CREATE UNIQUE INDEX xl_reports_name ON xl_reports (company_id, lower(name)) WHERE status = 'active';

CREATE TABLE xl_report_runs (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  report_id       uuid,
  report_key      text NOT NULL,
  params          jsonb NOT NULL DEFAULT '{}'::jsonb,
  definition      jsonb NOT NULL DEFAULT '{}'::jsonb,
  row_count       integer NOT NULL DEFAULT 0,
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, report_id) REFERENCES xl_reports (organization_id, company_id, id)
);
CREATE INDEX xl_report_runs_by_company ON xl_report_runs (company_id, created_at DESC);

-- ------------------------------------------------------------------ immutability
CREATE FUNCTION xl_guard_mapping() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN RAISE EXCEPTION 'an activated mapping is history and cannot be deleted' USING ERRCODE = 'integrity_constraint_violation'; END IF;
    RETURN OLD;
  END IF;
  IF OLD.status <> 'draft' THEN
    IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') OR NOT (OLD.status = 'active' AND NEW.status IN ('active', 'superseded') OR OLD.status = 'superseded' AND NEW.status = 'superseded') THEN
      RAISE EXCEPTION 'an activated mapping is immutable; create a new mapping version' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER xl_mappings_guard BEFORE UPDATE OR DELETE ON xl_mappings FOR EACH ROW EXECUTE FUNCTION xl_guard_mapping();

CREATE FUNCTION xl_guard_export() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'exports are never deleted' USING ERRCODE = 'integrity_constraint_violation'; END IF;
  IF OLD.status IN ('completed', 'failed') THEN
    RAISE EXCEPTION 'a % export is a historical record and cannot be changed', OLD.status USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER xl_exports_guard BEFORE UPDATE OR DELETE ON xl_exports FOR EACH ROW EXECUTE FUNCTION xl_guard_export();

-- ------------------------------------------------------------------ row-level security
DO $$
DECLARE
  t text;
  scope text := 'organization_id = app.org_id() AND (app.company_scope() = ''all'' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), ''{}''::uuid[])))';
BEGIN
  FOREACH t IN ARRAY ARRAY['xl_templates', 'xl_template_versions', 'xl_mappings', 'xl_exports', 'xl_export_rows', 'xl_reports', 'xl_report_runs'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO artify_app USING (%s)', t || '_select', t, scope);
    EXECUTE format('CREATE POLICY %I ON %I FOR INSERT TO artify_app WITH CHECK (%s)', t || '_insert', t, scope);
    IF t NOT IN ('xl_template_versions', 'xl_export_rows', 'xl_report_runs') THEN
      EXECUTE format('CREATE POLICY %I ON %I FOR UPDATE TO artify_app USING (%s) WITH CHECK (%s)', t || '_update', t, scope, scope);
    END IF;
    IF t IN ('xl_templates', 'xl_template_versions', 'xl_mappings') THEN
      EXECUTE format('CREATE POLICY %I ON %I FOR DELETE TO artify_app USING (%s)', t || '_delete', t, scope);
    END IF;
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON xl_templates, xl_mappings, xl_exports, xl_reports TO artify_app;
GRANT SELECT, INSERT ON xl_template_versions, xl_export_rows, xl_report_runs TO artify_app;   -- append-only
GRANT DELETE ON xl_templates, xl_template_versions, xl_mappings TO artify_app;                 -- only for never-used templates (FKs refuse otherwise)
-- no DELETE on exports, export rows, reports or runs: archive instead

GRANT SELECT ON xl_exports TO artify_definer;
CREATE POLICY definer_read ON xl_exports FOR SELECT TO artify_definer USING (organization_id = app.org_id());

-- entitlement measurement: earlier branches plus billable template/export storage and the export quota
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
  ELSIF p_key = 'quota.storage_bytes' THEN
    -- billable: stored originals, template files and generated exports that have not been purged (derived page images are not billed)
    SELECT COALESCE(sum(size_bytes), 0) INTO v FROM storage_objects
     WHERE organization_id = p_org AND state IN ('stored', 'verified') AND purpose IN ('original', 'template', 'export');
  ELSIF p_key = 'quota.documents_per_month' THEN
    SELECT count(*) INTO v FROM documents WHERE organization_id = p_org AND created_at >= date_trunc('month', now());
  ELSIF p_key = 'quota.ocr_pages_per_month' THEN
    SELECT COALESCE(sum(page_count), 0) INTO v FROM ocr_results WHERE organization_id = p_org AND created_at >= date_trunc('month', now());
  ELSIF p_key = 'quota.ai_extractions_per_month' THEN
    SELECT count(*) INTO v FROM extractions WHERE organization_id = p_org AND created_at >= date_trunc('month', now());
  ELSIF p_key = 'quota.bank_statements_per_month' THEN
    SELECT count(*) INTO v FROM bank_statements WHERE organization_id = p_org
       AND extraction_id IN (SELECT id FROM extractions WHERE organization_id = p_org AND created_at >= date_trunc('month', now()));
  ELSIF p_key = 'limit.processing_concurrency' THEN
    SELECT count(*) INTO v FROM processing_jobs WHERE organization_id = p_org
       AND status IN ('queued', 'preparing_pages', 'ocr_processing', 'classifying', 'extracting', 'validating', 'suggesting');
  ELSIF p_key = 'quota.accounting_transactions_per_month' THEN
    SELECT count(*) INTO v FROM acct_transactions WHERE organization_id = p_org AND reversal_of_id IS NULL AND created_at >= date_trunc('month', now());
  ELSIF p_key = 'quota.journals_per_month' THEN
    SELECT count(*) INTO v FROM acct_journals WHERE organization_id = p_org AND reversal_of_id IS NULL AND created_at >= date_trunc('month', now());
  ELSIF p_key = 'quota.excel_exports_per_month' THEN
    SELECT count(*) INTO v FROM xl_exports WHERE organization_id = p_org AND source_export_id IS NULL AND created_at >= date_trunc('month', now());
  ELSE
    RAISE EXCEPTION 'entitlement % is not measurable yet', p_key USING ERRCODE = 'feature_not_supported';
  END IF;
  RETURN v;
END $$;
