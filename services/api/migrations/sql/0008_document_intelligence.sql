-- Phase 7: OCR and AI document intelligence. Everything here is DERIVED from Phase 6 documents and never replaces them:
-- originals and versions stay untouched; every OCR run and extraction is a new, preserved record.

-- derived page images (preprocessed representations) live in the same object store but are not billed as stored documents
ALTER TABLE storage_objects ADD COLUMN purpose text NOT NULL DEFAULT 'original' CHECK (purpose IN ('original', 'derived'));

-- ------------------------------------------------------------------ provider configuration (no secrets in the database)
CREATE TABLE ai_provider_configurations (
  id                     uuid PRIMARY KEY,
  organization_id        uuid NOT NULL,
  company_id             uuid,
  kind                   text NOT NULL CHECK (kind IN ('ocr', 'understanding')),
  provider_key           text NOT NULL CHECK (provider_key ~ '^[a-z][a-z0-9_]{1,47}$'),
  enabled                boolean NOT NULL DEFAULT true,
  -- explicit consent: document CONTENT may be sent to a provider that runs outside Artify's infrastructure
  allow_external_content boolean NOT NULL DEFAULT false,
  options                jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(options) = 'object'),
  updated_by             uuid REFERENCES users (id),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE NULLS NOT DISTINCT (organization_id, company_id, kind)
);

-- ------------------------------------------------------------------ jobs
CREATE TABLE processing_jobs (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  document_id     uuid NOT NULL,
  version_id      uuid NOT NULL,
  version_no      integer NOT NULL,
  kind            text NOT NULL DEFAULT 'full' CHECK (kind IN ('full', 'ocr', 'classify', 'extract')),
  status          text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'preparing_pages', 'ocr_processing', 'classifying',
                    'extracting', 'validating', 'suggesting', 'awaiting_review', 'approved', 'failed', 'cancelled', 'needs_manual')),
  idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
  languages       text[] NOT NULL DEFAULT '{eng}',
  pages_total     integer,
  pages_done      integer NOT NULL DEFAULT 0,
  attempt_count   integer NOT NULL DEFAULT 0,
  max_attempts    integer NOT NULL DEFAULT 3,
  error_code      text,
  error_message   text,
  providers       jsonb NOT NULL DEFAULT '{}'::jsonb,
  reason          text CHECK (reason IS NULL OR length(reason) <= 500),
  requested_by    uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  started_at      timestamptz,
  finished_at     timestamptz,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, version_id) REFERENCES document_versions (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (company_id, requested_by, idempotency_key)
);
-- at most one ACTIVE job per document version and kind: a repeated click can never create duplicate extraction records
CREATE UNIQUE INDEX processing_jobs_one_active ON processing_jobs (version_id, kind)
  WHERE status IN ('queued', 'preparing_pages', 'ocr_processing', 'classifying', 'extracting', 'validating', 'suggesting');
CREATE INDEX processing_jobs_by_company ON processing_jobs (company_id, status, created_at DESC);
CREATE INDEX processing_jobs_by_document ON processing_jobs (document_id, created_at DESC);
CREATE INDEX processing_jobs_org_month ON processing_jobs (organization_id, created_at);

CREATE TABLE processing_attempts (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  job_id          uuid NOT NULL,
  attempt_no      integer NOT NULL,
  outcome         text NOT NULL DEFAULT 'running' CHECK (outcome IN ('running', 'succeeded', 'failed', 'timeout', 'cancelled')),
  error_code      text,
  error_message   text,
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  FOREIGN KEY (organization_id, company_id, job_id) REFERENCES processing_jobs (organization_id, company_id, id),
  UNIQUE (job_id, attempt_no)
);

-- ------------------------------------------------------------------ OCR output (kept per job; never overwritten)
CREATE TABLE ocr_results (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  job_id          uuid NOT NULL,
  document_id     uuid NOT NULL,
  version_id      uuid NOT NULL,
  provider_key    text NOT NULL,
  engine_version  text NOT NULL,
  languages       text[] NOT NULL,
  page_count      integer NOT NULL CHECK (page_count >= 0),
  avg_confidence  numeric(5, 2),
  full_text       text NOT NULL DEFAULT '',
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id, job_id) REFERENCES processing_jobs (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, version_id) REFERENCES document_versions (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (job_id)
);
CREATE INDEX ocr_results_by_document ON ocr_results (document_id, created_at DESC);

CREATE TABLE ocr_pages (
  id                  uuid PRIMARY KEY,
  organization_id     uuid NOT NULL,
  company_id          uuid NOT NULL,
  ocr_result_id       uuid NOT NULL,
  page_no             integer NOT NULL CHECK (page_no >= 1),
  width               integer NOT NULL,
  height              integer NOT NULL,
  rotation_applied    integer NOT NULL DEFAULT 0,
  deskew_angle        numeric(6, 2) NOT NULL DEFAULT 0,
  source              text NOT NULL CHECK (source IN ('pdf_text', 'ocr')),
  is_blank            boolean NOT NULL DEFAULT false,
  avg_confidence      numeric(5, 2),
  text                text NOT NULL DEFAULT '',
  quality             jsonb NOT NULL DEFAULT '{}'::jsonb,
  processed_object_id uuid,
  FOREIGN KEY (organization_id, company_id, ocr_result_id) REFERENCES ocr_results (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, processed_object_id) REFERENCES storage_objects (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (ocr_result_id, page_no)
);

CREATE TABLE ocr_blocks (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  page_id         uuid NOT NULL,
  block_no        integer NOT NULL,
  text            text NOT NULL,
  confidence      numeric(5, 2),
  bbox            jsonb NOT NULL,  -- [x0, y0, x1, y1] normalised to 0..1 of the page
  words           jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [[text, x0, x1, confidence], ...] for column assignment in tables
  FOREIGN KEY (organization_id, company_id, page_id) REFERENCES ocr_pages (organization_id, company_id, id),
  UNIQUE (page_id, block_no)
);

-- ------------------------------------------------------------------ classification
CREATE TABLE document_classifications (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  job_id          uuid NOT NULL,
  document_id     uuid NOT NULL,
  version_id      uuid NOT NULL,
  type_key        text NOT NULL,                       -- detected type (may be finer than the Artify document kind)
  kind_key        text REFERENCES document_kinds (key), -- the Artify document kind it maps to
  confidence      numeric(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  method          text NOT NULL,
  evidence        jsonb NOT NULL DEFAULT '[]'::jsonb,
  alternatives    jsonb NOT NULL DEFAULT '[]'::jsonb,
  needs_manual    boolean NOT NULL DEFAULT false,
  provider_key    text NOT NULL,
  model_version   text NOT NULL,
  review_status   text NOT NULL DEFAULT 'suggested' CHECK (review_status IN ('suggested', 'confirmed', 'corrected', 'rejected')),
  reviewed_by     uuid REFERENCES users (id),
  reviewed_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id, job_id) REFERENCES processing_jobs (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, version_id) REFERENCES document_versions (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id)
);
CREATE INDEX classifications_by_document ON document_classifications (document_id, created_at DESC);

-- ------------------------------------------------------------------ extraction (versioned; corrections and approval live here)
CREATE TABLE extractions (
  id                uuid PRIMARY KEY,
  organization_id   uuid NOT NULL,
  company_id        uuid NOT NULL,
  job_id            uuid NOT NULL,
  document_id       uuid NOT NULL,
  version_id        uuid NOT NULL,
  extraction_no     integer NOT NULL,
  classification_id uuid,
  type_key          text NOT NULL,
  schema_key        text NOT NULL,
  schema_version    integer NOT NULL,
  status            text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'superseded', 'rejected')),
  provider_key      text NOT NULL,
  model_version     text NOT NULL,
  prompt_version    text NOT NULL DEFAULT 'n/a',
  validation        jsonb NOT NULL DEFAULT '[]'::jsonb,
  summary           jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  approved_by       uuid REFERENCES users (id),
  approved_at       timestamptz,
  row_version       integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id, job_id) REFERENCES processing_jobs (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, version_id) REFERENCES document_versions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, classification_id) REFERENCES document_classifications (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (document_id, extraction_no),
  UNIQUE (job_id, schema_key),
  -- approval is a human act: the approver must be recorded whenever the status says approved
  CHECK (status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);
CREATE INDEX extractions_by_company ON extractions (company_id, status, created_at DESC);

CREATE TABLE extracted_fields (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  extraction_id   uuid NOT NULL,
  field_key       text NOT NULL,
  value           text,
  original_value  text,
  confidence      numeric(4, 3),
  page_no         integer,
  bbox            jsonb,
  source_text     text,
  method          text NOT NULL,
  review_status   text NOT NULL DEFAULT 'extracted' CHECK (review_status IN
                    ('extracted', 'needs_review', 'confirmed', 'corrected', 'missing', 'unreadable', 'rejected')),
  flags           jsonb NOT NULL DEFAULT '[]'::jsonb,
  reviewed_by     uuid REFERENCES users (id),
  reviewed_at     timestamptz,
  FOREIGN KEY (organization_id, company_id, extraction_id) REFERENCES extractions (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (extraction_id, field_key)
);

CREATE TABLE extracted_line_items (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  extraction_id   uuid NOT NULL,
  line_no         integer NOT NULL,
  page_no         integer,
  bbox            jsonb,
  source_text     text,
  "values"        jsonb NOT NULL,
  confidence      numeric(4, 3),
  review_status   text NOT NULL DEFAULT 'extracted' CHECK (review_status IN
                    ('extracted', 'needs_review', 'confirmed', 'corrected', 'missing', 'unreadable', 'rejected')),
  flags           jsonb NOT NULL DEFAULT '[]'::jsonb,
  FOREIGN KEY (organization_id, company_id, extraction_id) REFERENCES extractions (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (extraction_id, line_no)
);

-- ------------------------------------------------------------------ bank statements (extraction only; no reconciliation)
CREATE TABLE bank_statements (
  id                   uuid PRIMARY KEY,
  organization_id      uuid NOT NULL,
  company_id           uuid NOT NULL,
  extraction_id        uuid NOT NULL,
  bank_name            text,
  account_holder       text,
  account_name         text,
  account_number_masked text,   -- only the last digits are ever stored (data minimisation)
  iban_masked          text,
  currency             text,
  period_start         date,
  period_end           date,
  opening_balance      numeric(20, 4),
  closing_balance      numeric(20, 4),
  validation           jsonb NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY (organization_id, company_id, extraction_id) REFERENCES extractions (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (extraction_id)
);

CREATE TABLE bank_statement_transactions (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  statement_id    uuid NOT NULL,
  row_no          integer NOT NULL,
  page_no         integer,
  bbox            jsonb,
  source_text     text,
  tx_date         date,
  value_date      date,
  description     text,
  reference       text,
  debit           numeric(20, 4),
  credit          numeric(20, 4),
  balance         numeric(20, 4),
  currency        text,
  tx_type         text,
  confidence      numeric(4, 3),
  review_status   text NOT NULL DEFAULT 'extracted' CHECK (review_status IN
                    ('extracted', 'needs_review', 'confirmed', 'corrected', 'missing', 'unreadable', 'rejected')),
  manual          boolean NOT NULL DEFAULT false,
  flags           jsonb NOT NULL DEFAULT '[]'::jsonb,
  FOREIGN KEY (organization_id, company_id, statement_id) REFERENCES bank_statements (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (statement_id, row_no)
);

-- ------------------------------------------------------------------ suggestions (advisory; never applied automatically)
CREATE TABLE workspace_suggestions (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  extraction_id   uuid NOT NULL,
  rank            integer NOT NULL,
  node_id         uuid,
  category_id     uuid,
  confidence      numeric(4, 3) NOT NULL,
  method          text NOT NULL,
  rule_id         uuid,
  evidence        jsonb NOT NULL DEFAULT '[]'::jsonb,
  status          text NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested', 'accepted', 'rejected', 'unavailable')),
  FOREIGN KEY (organization_id, company_id, extraction_id) REFERENCES extractions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, node_id) REFERENCES workspace_nodes (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, category_id) REFERENCES document_categories (organization_id, company_id, id),
  UNIQUE (extraction_id, rank)
);

CREATE TABLE metadata_suggestions (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  extraction_id   uuid NOT NULL,
  field_id        uuid NOT NULL,
  value           jsonb,
  confidence      numeric(4, 3),
  evidence        jsonb NOT NULL DEFAULT '[]'::jsonb,
  problems        jsonb NOT NULL DEFAULT '[]'::jsonb,
  status          text NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested', 'accepted', 'rejected')),
  FOREIGN KEY (organization_id, company_id, extraction_id) REFERENCES extractions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, field_id) REFERENCES custom_field_definitions (organization_id, company_id, id),
  UNIQUE (extraction_id, field_id)
);

CREATE TABLE field_corrections (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  extraction_id   uuid NOT NULL,
  target_type     text NOT NULL CHECK (target_type IN ('field', 'line_item', 'transaction', 'classification', 'workspace', 'metadata')),
  target_id       uuid,
  field_key       text,
  action          text NOT NULL,
  old_value       text,
  new_value       text,
  reason          text,
  actor_id        uuid NOT NULL REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id, extraction_id) REFERENCES extractions (organization_id, company_id, id)
);
CREATE INDEX field_corrections_by_extraction ON field_corrections (extraction_id, created_at);

CREATE TABLE split_proposals (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  job_id          uuid NOT NULL,
  document_id     uuid NOT NULL,
  version_id      uuid NOT NULL,
  segments        jsonb NOT NULL,
  evidence        jsonb NOT NULL DEFAULT '[]'::jsonb,
  status          text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected')),
  decided_by      uuid REFERENCES users (id),
  decided_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id, job_id) REFERENCES processing_jobs (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, document_id) REFERENCES documents (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, version_id) REFERENCES document_versions (organization_id, company_id, id),
  UNIQUE (job_id)
);

-- ------------------------------------------------------------------ row-level security (same pattern as ADR-024)
DO $$
DECLARE
  t text;
  scope text := 'organization_id = app.org_id() AND (app.company_scope() = ''all'' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), ''{}''::uuid[])))';
  org_scope text := 'organization_id = app.org_id() AND (company_id IS NULL OR app.company_scope() = ''all'' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), ''{}''::uuid[])))';
BEGIN
  FOREACH t IN ARRAY ARRAY['processing_jobs', 'processing_attempts', 'ocr_results', 'ocr_pages', 'ocr_blocks', 'document_classifications',
                           'extractions', 'extracted_fields', 'extracted_line_items', 'bank_statements', 'bank_statement_transactions',
                           'workspace_suggestions', 'metadata_suggestions', 'field_corrections', 'split_proposals'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO artify_app USING (%s)', t || '_select', t, scope);
    EXECUTE format('CREATE POLICY %I ON %I FOR INSERT TO artify_app WITH CHECK (%s)', t || '_insert', t, scope);
    IF t <> 'field_corrections' THEN
      EXECUTE format('CREATE POLICY %I ON %I FOR UPDATE TO artify_app USING (%s) WITH CHECK (%s)', t || '_update', t, scope, scope);
    END IF;
  END LOOP;
  -- provider configuration: organization-wide rows (company_id NULL) are visible to the whole organization's staff
  ALTER TABLE ai_provider_configurations ENABLE ROW LEVEL SECURITY;
  ALTER TABLE ai_provider_configurations FORCE ROW LEVEL SECURITY;
  EXECUTE format('CREATE POLICY ai_provider_configurations_select ON ai_provider_configurations FOR SELECT TO artify_app USING (%s)', org_scope);
  EXECUTE format('CREATE POLICY ai_provider_configurations_insert ON ai_provider_configurations FOR INSERT TO artify_app WITH CHECK (%s)', org_scope);
  EXECUTE format('CREATE POLICY ai_provider_configurations_update ON ai_provider_configurations FOR UPDATE TO artify_app USING (%s) WITH CHECK (%s)', org_scope, org_scope);
  -- nothing here is ever DELETEd by the application: rejected rows keep their status, reprocessing adds new records
END $$;
GRANT SELECT, INSERT, UPDATE ON ai_provider_configurations, processing_jobs, processing_attempts, ocr_results, ocr_pages, ocr_blocks,
  document_classifications, extractions, extracted_fields, extracted_line_items, bank_statements, bank_statement_transactions,
  workspace_suggestions, metadata_suggestions, split_proposals TO artify_app;
GRANT SELECT, INSERT ON field_corrections TO artify_app;  -- append-only history

-- organization-wide processing quotas are measured by the narrow definer function
GRANT SELECT ON ocr_results, extractions, bank_statements, processing_jobs TO artify_definer;
CREATE POLICY definer_read ON ocr_results FOR SELECT TO artify_definer USING (organization_id = app.org_id());
CREATE POLICY definer_read ON extractions FOR SELECT TO artify_definer USING (organization_id = app.org_id());
CREATE POLICY definer_read ON bank_statements FOR SELECT TO artify_definer USING (organization_id = app.org_id());
CREATE POLICY definer_read ON processing_jobs FOR SELECT TO artify_definer USING (organization_id = app.org_id());

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
    -- billable: every stored ORIGINAL object that has not been purged (derived page images are not billed)
    SELECT COALESCE(sum(size_bytes), 0) INTO v FROM storage_objects
     WHERE organization_id = p_org AND state IN ('stored', 'verified') AND purpose = 'original';
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
  ELSE
    RAISE EXCEPTION 'entitlement % is not measurable yet', p_key USING ERRCODE = 'feature_not_supported';
  END IF;
  RETURN v;
END $$;
