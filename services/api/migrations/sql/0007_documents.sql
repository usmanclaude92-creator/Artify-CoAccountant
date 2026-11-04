-- Phase 6: document management and storage. Binary content lives in private object storage, never in PostgreSQL;
-- these tables hold metadata, ownership, versions, upload sessions, traceability and the audit trail.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ------------------------------------------------------------------ documents (stable id; one row per logical document)
CREATE TABLE documents (
  id                uuid PRIMARY KEY,
  organization_id   uuid NOT NULL,
  company_id        uuid NOT NULL,
  node_id           uuid NOT NULL,   -- logical location: a workspace node. Moving/renaming folders never touches storage.
  category_id       uuid,
  display_name      text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 200 AND display_name !~ '[/\\:*?"<>|[:cntrl:]]'),
  original_filename text NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  extension         text NOT NULL CHECK (extension ~ '^[a-z0-9]{1,10}$'),
  mime_type         text NOT NULL,
  size_bytes        bigint NOT NULL CHECK (size_bytes > 0),
  content_sha256    text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  current_version_no integer NOT NULL DEFAULT 1 CHECK (current_version_no >= 1),
  status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived', 'deleted', 'quarantined')),
  source            text NOT NULL DEFAULT 'desktop' CHECK (source IN ('desktop', 'web', 'mobile', 'email', 'api')),
  import_batch_id   uuid,
  filing_rule_id    uuid,            -- provenance of the destination suggestion (never AI in Phase 6)
  retention_hold    boolean NOT NULL DEFAULT false,
  created_by        uuid REFERENCES users (id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        uuid REFERENCES users (id),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  archived_at       timestamptz,
  deleted_at        timestamptz,
  deleted_by        uuid REFERENCES users (id),
  purged_at         timestamptz,
  row_version       integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, node_id) REFERENCES workspace_nodes (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, category_id) REFERENCES document_categories (organization_id, company_id, id)
);
CREATE INDEX documents_by_node ON documents (company_id, node_id, status, created_at DESC);
CREATE INDEX documents_by_company ON documents (company_id, status, created_at DESC, id);
CREATE UNIQUE INDEX documents_unique_name ON documents (company_id, node_id, lower(display_name)) WHERE status = 'active';
CREATE INDEX documents_by_sha ON documents (company_id, content_sha256);
CREATE INDEX documents_by_org_created ON documents (organization_id, created_at);
CREATE INDEX documents_name_trgm ON documents USING gin (lower(display_name) gin_trgm_ops);
CREATE INDEX documents_orig_trgm ON documents USING gin (lower(original_filename) gin_trgm_ops);

-- ------------------------------------------------------------------ storage objects (traceability DB <-> object store)
CREATE TABLE storage_objects (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  backend         text NOT NULL CHECK (backend IN ('local', 's3')),
  storage_key     text NOT NULL UNIQUE CHECK (storage_key ~ '^t/[0-9a-f-]{36}/c/[0-9a-f-]{36}/[a-z]/[0-9a-f-]{36}(/.+)?$'),
  size_bytes      bigint NOT NULL CHECK (size_bytes >= 0),
  sha256          text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  state           text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'stored', 'verified', 'missing', 'purged')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  verified_at     timestamptz,
  purged_at       timestamptz,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id)
);
CREATE INDEX storage_objects_org_state ON storage_objects (organization_id, state);

CREATE TABLE document_versions (
  id                 uuid PRIMARY KEY,
  organization_id    uuid NOT NULL,
  company_id         uuid NOT NULL,
  document_id        uuid NOT NULL,
  version_no         integer NOT NULL CHECK (version_no >= 1),
  original_filename  text NOT NULL,
  extension          text NOT NULL,
  mime_type          text NOT NULL,
  size_bytes         bigint NOT NULL CHECK (size_bytes > 0),
  sha256             text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  storage_object_id  uuid NOT NULL,
  uploaded_by        uuid REFERENCES users (id),
  uploaded_at        timestamptz NOT NULL DEFAULT now(),
  change_reason      text CHECK (change_reason IS NULL OR length(change_reason) <= 500),
  restored_from_version integer,
  scan_status        text NOT NULL DEFAULT 'not_scanned' CHECK (scan_status IN ('not_scanned', 'pending', 'clean', 'infected', 'error')),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, storage_object_id) REFERENCES storage_objects (organization_id, company_id, id),
  UNIQUE (document_id, version_no),
  UNIQUE (organization_id, company_id, id)
);

-- ------------------------------------------------------------------ custom metadata VALUES (definitions are Phase 4)
CREATE TABLE document_metadata_values (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  document_id     uuid NOT NULL,
  field_id        uuid NOT NULL,
  value           jsonb NOT NULL,
  updated_by      uuid REFERENCES users (id),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, field_id) REFERENCES custom_field_definitions (organization_id, company_id, id),
  UNIQUE (document_id, field_id)
);
CREATE INDEX document_metadata_field ON document_metadata_values (company_id, field_id, ((value #>> '{}')));

-- ------------------------------------------------------------------ import batches and upload sessions
CREATE TABLE document_import_batches (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  created_by      uuid REFERENCES users (id),
  source          text NOT NULL DEFAULT 'desktop',
  total_files     integer NOT NULL CHECK (total_files BETWEEN 1 AND 1000),
  client_batch_key text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id)
);

CREATE TABLE document_upload_operations (
  id                 uuid PRIMARY KEY,
  organization_id    uuid NOT NULL,
  company_id         uuid NOT NULL,
  batch_id           uuid,
  target_document_id uuid,           -- set when this upload is a NEW VERSION of an existing document
  status             text NOT NULL DEFAULT 'awaiting_upload' CHECK (status IN
                       ('awaiting_upload', 'uploading', 'uploaded', 'verifying', 'completed', 'failed', 'cancelled', 'quarantined', 'expired')),
  idempotency_key    text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
  original_filename  text NOT NULL,
  declared_size      bigint NOT NULL CHECK (declared_size > 0),
  declared_sha256    text CHECK (declared_sha256 IS NULL OR declared_sha256 ~ '^[0-9a-f]{64}$'),
  declared_mime      text,
  node_id            uuid,
  category_id        uuid,
  display_name       text,
  metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
  filing_rule_id     uuid,
  duplicate_policy   text NOT NULL DEFAULT 'block' CHECK (duplicate_policy IN ('block', 'allow_copy')),
  verified_mime      text,
  scan_status        text NOT NULL DEFAULT 'not_scanned' CHECK (scan_status IN ('not_scanned', 'pending', 'clean', 'infected', 'error')),
  change_reason      text,
  storage_object_id  uuid,
  received_size      bigint,
  received_sha256    text,
  error_code         text,
  error_message      text,
  attempts           integer NOT NULL DEFAULT 0,
  expires_at         timestamptz NOT NULL,
  created_by         uuid REFERENCES users (id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  completed_at       timestamptz,
  result_document_id uuid,
  result_version_id  uuid,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, batch_id) REFERENCES document_import_batches (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, node_id) REFERENCES workspace_nodes (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (company_id, created_by, idempotency_key)
);
CREATE INDEX upload_ops_status ON document_upload_operations (company_id, status, created_at DESC);
CREATE INDEX upload_ops_expiry ON document_upload_operations (status, expires_at);

-- ------------------------------------------------------------------ activity and access trail (append-only)
CREATE TABLE document_events (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  document_id     uuid,
  version_no      integer,
  actor_id        uuid REFERENCES users (id),
  event_type      text NOT NULL,
  summary         jsonb NOT NULL DEFAULT '{}'::jsonb,
  ip              inet,
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id)
);
CREATE INDEX document_events_doc ON document_events (document_id, created_at DESC);
CREATE INDEX document_events_company ON document_events (company_id, created_at DESC);

-- ------------------------------------------------------------------ row-level security (ADR-024 pattern)
DO $$
DECLARE
  t text;
  scope text := 'organization_id = app.org_id() AND (app.company_scope() = ''all'' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), ''{}''::uuid[])))';
BEGIN
  FOREACH t IN ARRAY ARRAY['documents', 'storage_objects', 'document_versions', 'document_metadata_values', 'document_import_batches',
                           'document_upload_operations', 'document_events'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO artify_app USING (%s)', t || '_select', t, scope);
    EXECUTE format('CREATE POLICY %I ON %I FOR INSERT TO artify_app WITH CHECK (%s)', t || '_insert', t, scope);
    IF t <> 'document_events' THEN
      EXECUTE format('CREATE POLICY %I ON %I FOR UPDATE TO artify_app USING (%s) WITH CHECK (%s)', t || '_update', t, scope, scope);
    END IF;
  END LOOP;
  EXECUTE format('CREATE POLICY document_metadata_values_delete ON document_metadata_values FOR DELETE TO artify_app USING (%s)', scope);
END $$;
GRANT SELECT, INSERT, UPDATE ON documents, storage_objects, document_versions, document_import_batches, document_upload_operations TO artify_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON document_metadata_values TO artify_app;
GRANT SELECT, INSERT ON document_events TO artify_app;   -- the trail cannot be rewritten through the application role

-- organization-wide storage accounting must see EVERY company, whatever the caller may reach: a narrow definer read
GRANT SELECT ON documents, storage_objects TO artify_definer;
CREATE POLICY definer_read ON documents FOR SELECT TO artify_definer USING (organization_id = app.org_id());
CREATE POLICY definer_read ON storage_objects FOR SELECT TO artify_definer USING (organization_id = app.org_id());

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
    -- billable: every stored original/version object that has not been purged (archived and soft-deleted documents count)
    SELECT COALESCE(sum(size_bytes), 0) INTO v FROM storage_objects WHERE organization_id = p_org AND state IN ('stored', 'verified');
  ELSIF p_key = 'quota.documents_per_month' THEN
    SELECT count(*) INTO v FROM documents WHERE organization_id = p_org AND created_at >= date_trunc('month', now());
  ELSE
    RAISE EXCEPTION 'entitlement % is not measurable yet', p_key USING ERRCODE = 'feature_not_supported';
  END IF;
  RETURN v;
END $$;
