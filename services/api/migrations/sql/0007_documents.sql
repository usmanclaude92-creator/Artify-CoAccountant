-- Phase 6: document management and storage engine.
-- Binary content is NEVER stored in PostgreSQL: only metadata and an opaque storage key. Every table carries the
-- composite (organization_id, company_id) FK and forced RLS (ADR-024 pattern). Documents reference workspace nodes and
-- categories by stable id with RESTRICT, so a node that holds documents can only be archived, never deleted.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ------------------------------------------------------------------ import batches (a group of files imported together)
CREATE TABLE document_import_batches (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  created_by      uuid NOT NULL REFERENCES users (id),
  device_id       uuid REFERENCES devices (id),
  source          text NOT NULL CHECK (source IN ('desktop', 'web', 'api', 'mobile')),
  label           text CHECK (label IS NULL OR length(label) <= 200),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id)
);

-- ------------------------------------------------------------------ documents (stable identity of a business document)
CREATE TABLE documents (
  id                     uuid PRIMARY KEY,
  organization_id        uuid NOT NULL,
  company_id             uuid NOT NULL,
  node_id                uuid NOT NULL,
  category_id            uuid,
  display_name           text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 255
                                              AND display_name !~ '[/\\:*?"<>|[:cntrl:]]'
                                              AND btrim(display_name) NOT IN ('.', '..')),
  original_filename      text NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  extension              text NOT NULL CHECK (extension ~ '^[a-z0-9]{1,10}$'),
  status                 text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived', 'deleted', 'purged')),
  current_version_id     uuid,
  current_version_number integer NOT NULL DEFAULT 1 CHECK (current_version_number >= 1),
  source                 text NOT NULL CHECK (source IN ('desktop', 'web', 'api', 'mobile', 'inbox')),
  import_batch_id        uuid,
  kind_at_filing         text,                      -- Artify document kind of the category when it was filed (snapshot)
  filed_path_snapshot    text,                      -- human-readable path when filed; NEVER used as a reference
  filing_rule_id         uuid,                      -- the rule that produced the suggestion that was accepted, if any
  legal_hold             boolean NOT NULL DEFAULT false,
  created_by             uuid NOT NULL REFERENCES users (id),
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_by             uuid REFERENCES users (id),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  archived_at            timestamptz,
  archived_by            uuid REFERENCES users (id),
  deleted_at             timestamptz,
  deleted_by             uuid REFERENCES users (id),
  delete_reason          text CHECK (delete_reason IS NULL OR length(delete_reason) <= 500),
  purged_at              timestamptz,
  row_version            integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, node_id) REFERENCES workspace_nodes (organization_id, company_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, company_id, category_id) REFERENCES document_categories (organization_id, company_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, company_id, import_batch_id) REFERENCES document_import_batches (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id)
);
CREATE INDEX documents_company_node ON documents (company_id, node_id, status);
CREATE INDEX documents_company_created ON documents (company_id, created_at DESC, id);
CREATE INDEX documents_org_created ON documents (organization_id, created_at);
CREATE INDEX documents_name_trgm ON documents USING gin (lower(display_name) gin_trgm_ops);

-- ------------------------------------------------------------------ versions (every binary ever stored; immutable)
CREATE TABLE document_versions (
  id                        uuid PRIMARY KEY,
  organization_id           uuid NOT NULL,
  company_id                uuid NOT NULL,
  document_id               uuid NOT NULL,
  version_number            integer NOT NULL CHECK (version_number >= 1),
  original_filename         text NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  extension                 text NOT NULL CHECK (extension ~ '^[a-z0-9]{1,10}$'),
  mime_type                 text NOT NULL,                 -- derived from the CONTENT, never from the client
  size_bytes                bigint NOT NULL CHECK (size_bytes > 0),
  sha256                    text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  storage_driver            text NOT NULL,
  storage_key               text NOT NULL CHECK (storage_key ~ '^[a-z0-9/_-]{20,200}$'),   -- opaque; no filename, no path
  scan_status               text NOT NULL DEFAULT 'not_scanned' CHECK (scan_status IN ('not_scanned', 'clean', 'infected', 'pending')),
  change_reason             text CHECK (change_reason IS NULL OR length(change_reason) <= 500),
  uploaded_by               uuid NOT NULL REFERENCES users (id),
  created_at                timestamptz NOT NULL DEFAULT now(),
  purged_at                 timestamptz,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id),
  UNIQUE (document_id, version_number),
  UNIQUE (storage_key),
  UNIQUE (organization_id, company_id, id)
);
CREATE INDEX document_versions_company_sha ON document_versions (company_id, sha256);
CREATE INDEX document_versions_org_size ON document_versions (organization_id) WHERE purged_at IS NULL;
ALTER TABLE documents ADD CONSTRAINT documents_current_version_fk
  FOREIGN KEY (current_version_id) REFERENCES document_versions (id) DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION app.guard_document_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'document versions are never deleted' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  -- only the scan result and the purge marker may change; the binary identity of a version is immutable
  IF (NEW.id, NEW.organization_id, NEW.company_id, NEW.document_id, NEW.version_number, NEW.original_filename, NEW.extension,
      NEW.mime_type, NEW.size_bytes, NEW.sha256, NEW.storage_driver, NEW.storage_key, NEW.change_reason,
      NEW.uploaded_by, NEW.created_at)
     IS DISTINCT FROM
     (OLD.id, OLD.organization_id, OLD.company_id, OLD.document_id, OLD.version_number, OLD.original_filename, OLD.extension,
      OLD.mime_type, OLD.size_bytes, OLD.sha256, OLD.storage_driver, OLD.storage_key, OLD.change_reason,
      OLD.uploaded_by, OLD.created_at) THEN
    RAISE EXCEPTION 'a stored document version is immutable' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.purged_at IS NOT NULL AND NEW.purged_at IS DISTINCT FROM OLD.purged_at THEN
    RAISE EXCEPTION 'a purged version stays purged' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER document_versions_guard BEFORE UPDATE OR DELETE ON document_versions
  FOR EACH ROW EXECUTE FUNCTION app.guard_document_version();

-- ------------------------------------------------------------------ custom field values (typed; keys come from Phase 4 definitions)
CREATE TABLE document_field_values (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  document_id     uuid NOT NULL,
  field_id        uuid NOT NULL,
  field_key       text NOT NULL,
  data_type       text NOT NULL,
  value           jsonb NOT NULL,                  -- normalized by the Phase 4 validator
  value_text      text,                            -- text/dropdown/reference/multi-select (joined) for filtering
  value_number    numeric(28, 6),                  -- number / decimal / currency amount (exact)
  value_date      date,
  updated_by      uuid REFERENCES users (id),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, field_id) REFERENCES custom_field_definitions (organization_id, company_id, id),
  UNIQUE (document_id, field_id)
);
CREATE INDEX document_field_values_filter ON document_field_values (company_id, field_id, value_text);

-- ------------------------------------------------------------------ upload sessions (resumable, idempotent)
CREATE TABLE document_upload_sessions (
  id                uuid PRIMARY KEY,
  organization_id   uuid NOT NULL,
  company_id        uuid NOT NULL,
  batch_id          uuid,
  created_by        uuid NOT NULL REFERENCES users (id),
  device_id         uuid REFERENCES devices (id),
  node_id           uuid NOT NULL,
  category_id       uuid,
  document_id       uuid,                          -- set when the upload adds a version to an existing document
  original_filename text NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  declared_size     bigint NOT NULL CHECK (declared_size > 0),
  declared_sha256   text CHECK (declared_sha256 IS NULL OR declared_sha256 ~ '^[0-9a-f]{64}$'),
  received_bytes    bigint NOT NULL DEFAULT 0 CHECK (received_bytes >= 0),
  status            text NOT NULL DEFAULT 'open' CHECK (status IN
                      ('open', 'uploading', 'verifying', 'completed', 'failed', 'cancelled', 'expired', 'quarantined')),
  staging_ref       text,
  source            text NOT NULL CHECK (source IN ('desktop', 'web', 'api', 'mobile', 'inbox')),
  display_name      text,
  field_values      jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(field_values) = 'object'),
  change_reason     text,
  duplicate_policy  text NOT NULL DEFAULT 'block' CHECK (duplicate_policy IN ('block', 'allow_copy')),
  filing_rule_id    uuid,
  result_document_id uuid,
  result_version_id  uuid,
  error_code        text,
  error_message     text,
  idempotency_key   text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
  expires_at        timestamptz NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, node_id) REFERENCES workspace_nodes (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, batch_id) REFERENCES document_import_batches (organization_id, company_id, id),
  UNIQUE (company_id, created_by, idempotency_key),
  UNIQUE (organization_id, company_id, id)
);
CREATE INDEX document_upload_sessions_status ON document_upload_sessions (company_id, status, created_at DESC);

-- ------------------------------------------------------------------ activity and access events (append-only)
CREATE TABLE document_events (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  document_id     uuid NOT NULL,
  version_id      uuid,
  event_type      text NOT NULL CHECK (event_type IN (
                    'uploaded', 'version_created', 'version_restored', 'renamed', 'moved', 'metadata_changed', 'archived',
                    'restored', 'deleted', 'undeleted', 'purged', 'downloaded', 'previewed', 'legal_hold_changed',
                    'duplicate_kept', 'quarantined')),
  actor_id        uuid REFERENCES users (id),
  actor_type      text NOT NULL DEFAULT 'user' CHECK (actor_type IN ('user', 'system')),
  device_id       uuid REFERENCES devices (id),
  detail          jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id)
);
CREATE INDEX document_events_doc ON document_events (document_id, created_at DESC);
CREATE INDEX document_events_company ON document_events (company_id, created_at DESC);
CREATE TRIGGER document_events_immutable BEFORE UPDATE OR DELETE ON document_events
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();

-- ------------------------------------------------------------------ duplicate decisions (the queue Phase 8 extends)
CREATE TABLE document_duplicate_candidates (
  id                     uuid PRIMARY KEY,
  organization_id        uuid NOT NULL,
  company_id             uuid NOT NULL,
  document_id            uuid NOT NULL,
  duplicate_of_document_id uuid NOT NULL,
  sha256                 text NOT NULL,
  decision               text NOT NULL CHECK (decision IN ('kept_separate')),
  decided_by             uuid REFERENCES users (id),
  created_at             timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, duplicate_of_document_id) REFERENCES documents (organization_id, company_id, id)
);
CREATE TRIGGER document_duplicate_candidates_immutable BEFORE UPDATE OR DELETE ON document_duplicate_candidates
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();

-- ------------------------------------------------------------------ row-level security (ADR-024 pattern) and grants
DO $$
DECLARE
  t text;
  scope text := 'organization_id = app.org_id() AND (app.company_scope() = ''all'' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), ''{}''::uuid[])))';
BEGIN
  FOREACH t IN ARRAY ARRAY['document_import_batches', 'documents', 'document_versions', 'document_field_values',
                           'document_upload_sessions', 'document_events', 'document_duplicate_candidates'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO artify_app USING (%s)', t || '_select', t, scope);
    EXECUTE format('CREATE POLICY %I ON %I FOR INSERT TO artify_app WITH CHECK (%s)', t || '_insert', t, scope);
    IF t NOT IN ('document_events', 'document_duplicate_candidates', 'document_import_batches') THEN
      EXECUTE format('CREATE POLICY %I ON %I FOR UPDATE TO artify_app USING (%s) WITH CHECK (%s)', t || '_update', t, scope, scope);
    END IF;
  END LOOP;
  EXECUTE format('CREATE POLICY document_field_values_delete ON document_field_values FOR DELETE TO artify_app USING (%s)', scope);
END $$;
-- documents, versions and events can never be deleted by the application role (soft delete and purge are states)
GRANT SELECT, INSERT, UPDATE ON documents, document_versions, document_field_values, document_upload_sessions TO artify_app;
GRANT DELETE ON document_field_values TO artify_app;
GRANT SELECT, INSERT ON document_import_batches, document_events, document_duplicate_candidates TO artify_app;

-- the narrow, organization-bound measurement function reads versions and documents as the definer
GRANT SELECT ON document_versions, documents TO artify_definer;
CREATE POLICY definer_read ON document_versions FOR SELECT TO artify_definer USING (organization_id = app.org_id());
CREATE POLICY definer_read ON documents FOR SELECT TO artify_definer USING (organization_id = app.org_id());

-- ------------------------------------------------------------------ storage accounting (see docs/documents/STORAGE_ENTITLEMENTS.md)
-- quota.storage_bytes = bytes of every stored version of every document whose binary has not been purged:
-- archived and soft-deleted documents and all retained versions count; restoring a previous version only switches the
-- document's current-version pointer and creates no new bytes.
-- quota.documents_per_month = documents created in the current UTC month (versions do not count).
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
    SELECT COALESCE(sum(size_bytes), 0) INTO v FROM document_versions WHERE organization_id = p_org AND purged_at IS NULL;
  ELSIF p_key = 'quota.documents_per_month' THEN
    SELECT count(*) INTO v FROM documents
     WHERE organization_id = p_org AND created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
  ELSE
    RAISE EXCEPTION 'entitlement % is not measurable yet', p_key USING ERRCODE = 'feature_not_supported';
  END IF;
  RETURN v;
END $$;

-- ------------------------------------------------------------------ usage counters for the new metered keys
-- Every organization (existing and future, however it is created) gets zero-valued counters, so the platform usage view
-- shows a real 0 and not "not metered" for keys that are enforced.
INSERT INTO usage_counters (id, organization_id, metric, value)
SELECT gen_random_uuid(), o.id, k, 0 FROM organizations o CROSS JOIN unnest(ARRAY['quota.storage_bytes', 'quota.documents_per_month']) AS k
ON CONFLICT (organization_id, metric, scope_key, period_key) DO NOTHING;

CREATE FUNCTION app.seed_document_counters() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  INSERT INTO usage_counters (id, organization_id, metric, value)
  SELECT gen_random_uuid(), NEW.id, k, 0 FROM unnest(ARRAY['quota.storage_bytes', 'quota.documents_per_month']) AS k;
  RETURN NEW;  -- a brand-new organization has no counters yet, so a plain insert is correct (and needs no SELECT right)
END $$;
ALTER FUNCTION app.seed_document_counters() OWNER TO artify_definer;
REVOKE ALL ON FUNCTION app.seed_document_counters() FROM PUBLIC;
CREATE TRIGGER organizations_seed_document_counters AFTER INSERT ON organizations
  FOR EACH ROW EXECUTE FUNCTION app.seed_document_counters();
