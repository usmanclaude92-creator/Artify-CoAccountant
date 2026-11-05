-- Phase 10: Daily bookkeeping and closing.
-- Everything is company-owned (organization_id + company_id, composite FKs, forced RLS). Nothing is shared between companies:
-- checklist templates are COPIED into a company and edited there. Closing snapshots, approvals, validation runs and audit events
-- are append-only; a closed day is protected in the database (exceptions, completions, transaction approval) until it is
-- explicitly reopened, which keeps the original closing and starts a new, linked one.

-- ------------------------------------------------------------------ policy (per company)
CREATE TABLE bk_policies (
  company_id                  uuid PRIMARY KEY,
  organization_id             uuid NOT NULL,
  require_independent_approval boolean NOT NULL DEFAULT true,   -- the person who submits a closing cannot approve and close it
  require_independent_task_review boolean NOT NULL DEFAULT true,
  action_roles                jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(action_roles) = 'object'),   -- action -> [role keys]; absent = any holder of the permission
  severity_overrides          jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(severity_overrides) = 'object'), -- validation code -> severity
  override_min_chars          integer NOT NULL DEFAULT 10 CHECK (override_min_chars BETWEEN 5 AND 500),
  timezone                    text CHECK (timezone IS NULL OR length(timezone) BETWEEN 1 AND 64),   -- the company's own business-day zone; NULL = the organization's
  updated_by                  uuid REFERENCES users (id),
  updated_at                  timestamptz NOT NULL DEFAULT now(),
  row_version                 integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id)
);

-- ------------------------------------------------------------------ checklists
CREATE TABLE bk_checklists (
  id                  uuid PRIMARY KEY,
  organization_id     uuid NOT NULL,
  company_id          uuid NOT NULL,
  name                text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  description         text CHECK (description IS NULL OR length(description) <= 500),
  position            integer NOT NULL DEFAULT 0,
  source_template_key text,        -- provenance only; the copy never follows the template
  archived_at         timestamptz,
  created_by          uuid REFERENCES users (id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  row_version         integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id)
);
CREATE UNIQUE INDEX bk_checklists_name ON bk_checklists (company_id, lower(name)) WHERE archived_at IS NULL;

CREATE TABLE bk_checklist_items (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  checklist_id    uuid NOT NULL,
  title           text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  description     text CHECK (description IS NULL OR length(description) <= 500),
  position        integer NOT NULL DEFAULT 0,
  mandatory       boolean NOT NULL DEFAULT false,
  assigned_role   text CHECK (assigned_role IS NULL OR assigned_role ~ '^[a-z][a-z_]{1,39}$'),
  auto_rule       text CHECK (auto_rule IS NULL OR auto_rule ~ '^[a-z][a-z_]{1,39}$'),
  archived_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, checklist_id) REFERENCES bk_checklists (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id)
);
CREATE INDEX bk_checklist_items_by_list ON bk_checklist_items (checklist_id, position);

CREATE TABLE bk_checklist_completions (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  item_id         uuid NOT NULL,
  business_date   date NOT NULL,
  completed_by    uuid NOT NULL REFERENCES users (id),
  completed_at    timestamptz NOT NULL DEFAULT now(),
  note            text CHECK (note IS NULL OR length(note) <= 500),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, item_id) REFERENCES bk_checklist_items (organization_id, company_id, id),
  UNIQUE (item_id, business_date)
);
CREATE INDEX bk_completions_by_date ON bk_checklist_completions (company_id, business_date);

-- ------------------------------------------------------------------ tasks
CREATE TABLE bk_tasks (
  id               uuid PRIMARY KEY,
  organization_id  uuid NOT NULL,
  company_id       uuid NOT NULL,
  title            text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  description      text CHECK (description IS NULL OR length(description) <= 2000),
  status           text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'assigned', 'in_progress', 'awaiting_information',
                     'awaiting_review', 'approved', 'rejected', 'completed', 'cancelled')),
  priority         text NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
  category         text CHECK (category IS NULL OR category ~ '^[a-z][a-z0-9_]{1,59}$'),   -- free text chosen by the company; nothing depends on it
  reviewer_id      uuid REFERENCES users (id),                                              -- who is asked to review; the review permission is still required
  business_date    date,
  due_date         date,
  requires_review  boolean NOT NULL DEFAULT false,
  blocks_closing   boolean NOT NULL DEFAULT false,
  independence_required boolean NOT NULL DEFAULT false,   -- copied from the policy when review is requested
  subject_type     text CHECK (subject_type IS NULL OR subject_type IN ('document', 'transaction', 'exception', 'closing')),
  subject_id       uuid,
  assignee_id      uuid REFERENCES users (id),
  review_requested_by uuid REFERENCES users (id),
  approved_by      uuid REFERENCES users (id),
  rejection_reason text CHECK (rejection_reason IS NULL OR length(rejection_reason) <= 500),
  created_by       uuid REFERENCES users (id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  completed_at     timestamptz,
  row_version      integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id),
  CHECK ((subject_type IS NULL) = (subject_id IS NULL)),
  CHECK (status NOT IN ('approved') OR approved_by IS NOT NULL),
  -- independence: where required, the person who asked for the review cannot be the person who approved it
  CHECK (NOT independence_required OR approved_by IS DISTINCT FROM review_requested_by OR approved_by IS NULL)
);
CREATE INDEX bk_tasks_by_company ON bk_tasks (company_id, status, due_date);
CREATE INDEX bk_tasks_by_assignee ON bk_tasks (company_id, assignee_id) WHERE assignee_id IS NOT NULL;
CREATE INDEX bk_tasks_by_date ON bk_tasks (company_id, business_date) WHERE business_date IS NOT NULL;

CREATE TABLE bk_task_assignments (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  task_id         uuid NOT NULL,
  assignee_id     uuid REFERENCES users (id),
  assigned_by     uuid REFERENCES users (id),
  assigned_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, task_id) REFERENCES bk_tasks (organization_id, company_id, id)
);
CREATE INDEX bk_task_assignments_by_task ON bk_task_assignments (task_id, assigned_at);

CREATE TABLE bk_task_comments (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  task_id         uuid NOT NULL,
  body            text NOT NULL CHECK (length(btrim(body)) BETWEEN 1 AND 2000),
  author_id       uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, task_id) REFERENCES bk_tasks (organization_id, company_id, id)
);
CREATE INDEX bk_task_comments_by_task ON bk_task_comments (task_id, created_at);

-- ------------------------------------------------------------------ closing sessions
CREATE TABLE bk_closing_sessions (
  id                 uuid PRIMARY KEY,
  organization_id    uuid NOT NULL,
  company_id         uuid NOT NULL,
  business_date      date NOT NULL,
  period_id          uuid,
  status             text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'in_progress', 'ready_for_review', 'closed', 'reopened', 'cancelled')),
  stage              integer NOT NULL DEFAULT 1 CHECK (stage BETWEEN 1 AND 6),
  validation_status  text CHECK (validation_status IS NULL OR validation_status IN ('passed', 'passed_with_warnings', 'failed', 'awaiting_review')),
  independence_required boolean NOT NULL DEFAULT true,
  summary            jsonb NOT NULL DEFAULT '{}'::jsonb,
  prepared_by        uuid REFERENCES users (id),
  prepared_at        timestamptz,
  submitted_by       uuid REFERENCES users (id),
  submitted_at       timestamptz,
  review_note        text CHECK (review_note IS NULL OR length(review_note) <= 500),
  closed_by          uuid REFERENCES users (id),
  closed_at          timestamptz,
  reopened_by        uuid REFERENCES users (id),
  reopened_at        timestamptz,
  reopen_reason      text CHECK (reopen_reason IS NULL OR length(reopen_reason) <= 500),
  cancelled_by       uuid REFERENCES users (id),
  cancelled_at       timestamptz,
  cancel_reason      text CHECK (cancel_reason IS NULL OR length(cancel_reason) <= 500),
  reopened_from_id   uuid,
  created_by         uuid REFERENCES users (id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  row_version        integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, period_id) REFERENCES acct_periods (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, reopened_from_id) REFERENCES bk_closing_sessions (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id),
  CHECK (status NOT IN ('ready_for_review', 'closed') OR (submitted_by IS NOT NULL AND submitted_at IS NOT NULL)),
  CHECK (status <> 'closed' OR (closed_by IS NOT NULL AND closed_at IS NOT NULL AND validation_status IN ('passed', 'passed_with_warnings'))),
  -- separation of duties, recorded on the row so a bug cannot silently skip it
  CHECK (status <> 'closed' OR NOT independence_required OR closed_by IS DISTINCT FROM submitted_by),
  CHECK (status <> 'reopened' OR (reopened_by IS NOT NULL AND reopened_at IS NOT NULL AND reopen_reason IS NOT NULL)),
  CHECK (status <> 'cancelled' OR (cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL))
);
-- one live closing per company and day; a reopened or cancelled one is history
CREATE UNIQUE INDEX bk_sessions_one_live ON bk_closing_sessions (company_id, business_date)
  WHERE status IN ('draft', 'in_progress', 'ready_for_review', 'closed');
CREATE INDEX bk_sessions_by_company ON bk_closing_sessions (company_id, business_date DESC);

CREATE FUNCTION app.bk_guard_session() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ok boolean;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'a closing session is never deleted' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.status <> OLD.status THEN
    ok := (OLD.status, NEW.status) IN (
      ('draft', 'in_progress'), ('draft', 'cancelled'),
      ('in_progress', 'ready_for_review'), ('in_progress', 'cancelled'),
      ('ready_for_review', 'in_progress'), ('ready_for_review', 'closed'), ('ready_for_review', 'cancelled'),
      ('closed', 'reopened'));
    IF NOT ok THEN
      RAISE EXCEPTION 'a closing cannot go from % to %', OLD.status, NEW.status USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  IF OLD.status IN ('closed', 'reopened', 'cancelled') THEN
    -- history: the only change allowed is closed -> reopened (with its reason); nothing else may move
    IF NOT (OLD.status = 'closed' AND NEW.status = 'reopened') AND (to_jsonb(NEW) - 'row_version' - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'row_version' - 'updated_at') THEN
      RAISE EXCEPTION 'a % closing is a historical record and cannot be changed', OLD.status USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status = 'closed' AND NEW.status = 'reopened' AND
       (to_jsonb(NEW) - 'status' - 'reopened_by' - 'reopened_at' - 'reopen_reason' - 'row_version' - 'updated_at')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'status' - 'reopened_by' - 'reopened_at' - 'reopen_reason' - 'row_version' - 'updated_at') THEN
      RAISE EXCEPTION 'reopening may only record who reopened it and why' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
  END IF;
  IF NEW.business_date <> OLD.business_date OR NEW.company_id <> OLD.company_id THEN
    RAISE EXCEPTION 'a closing belongs to one company and one day' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER bk_sessions_guard BEFORE UPDATE OR DELETE ON bk_closing_sessions
  FOR EACH ROW EXECUTE FUNCTION app.bk_guard_session();

-- a day is closed while a closing for it is in status 'closed' (a reopened closing no longer protects the day)
CREATE FUNCTION app.bk_day_closed(p_company uuid, p_date date) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM bk_closing_sessions WHERE company_id = p_company AND business_date = p_date AND status = 'closed')
$$;

-- ------------------------------------------------------------------ validation runs and exceptions
CREATE TABLE bk_validation_runs (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  session_id      uuid,
  business_date   date NOT NULL,
  result          text NOT NULL CHECK (result IN ('passed', 'passed_with_warnings', 'failed', 'awaiting_review')),
  counts          jsonb NOT NULL DEFAULT '{}'::jsonb,
  issues          jsonb NOT NULL DEFAULT '[]'::jsonb,
  facts           jsonb NOT NULL DEFAULT '{}'::jsonb,
  run_by          uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, session_id) REFERENCES bk_closing_sessions (organization_id, company_id, id),
  UNIQUE (organization_id, company_id, id)
);
CREATE INDEX bk_validation_runs_by_date ON bk_validation_runs (company_id, business_date, created_at DESC);

CREATE TABLE bk_exceptions (
  id               uuid PRIMARY KEY,
  organization_id  uuid NOT NULL,
  company_id       uuid NOT NULL,
  business_date    date NOT NULL,
  code             text NOT NULL CHECK (code ~ '^[A-Z][A-Z0-9_]{2,59}$'),
  severity         text NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'critical')),
  subject_type     text NOT NULL DEFAULT 'day',
  subject_id       uuid,
  fingerprint      text NOT NULL,
  message          text NOT NULL CHECK (length(message) <= 500),
  data             jsonb NOT NULL DEFAULT '{}'::jsonb,
  status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'overridden', 'acknowledged')),
  resolution_note  text CHECK (resolution_note IS NULL OR length(resolution_note) <= 500),
  resolved_by      uuid REFERENCES users (id),
  resolved_at      timestamptz,
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (organization_id, company_id, id),
  UNIQUE (company_id, business_date, fingerprint),
  -- an override is a person's decision with an explanation
  CHECK (status NOT IN ('overridden') OR (resolved_by IS NOT NULL AND resolution_note IS NOT NULL AND resolved_at IS NOT NULL))
);
CREATE INDEX bk_exceptions_by_date ON bk_exceptions (company_id, business_date, status);

-- ------------------------------------------------------------------ approvals, snapshots, exports, sync, audit
CREATE TABLE bk_closing_approvals (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  session_id      uuid NOT NULL,
  action          text NOT NULL CHECK (action IN ('prepare', 'validate', 'submit', 'send_back', 'approve_close', 'reopen', 'override', 'cancel')),
  from_status     text,
  to_status       text,
  actor_type      text NOT NULL DEFAULT 'user' CHECK (actor_type IN ('user', 'system', 'ai')),
  actor_id        uuid REFERENCES users (id),
  note            text CHECK (note IS NULL OR length(note) <= 500),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, session_id) REFERENCES bk_closing_sessions (organization_id, company_id, id),
  -- structural: approving, closing, reopening and overriding are human decisions (invariant 6)
  CHECK (action NOT IN ('approve_close', 'reopen', 'override', 'submit', 'send_back') OR (actor_type = 'user' AND actor_id IS NOT NULL))
);
CREATE INDEX bk_closing_approvals_by_session ON bk_closing_approvals (session_id, created_at);

CREATE TABLE bk_closing_snapshots (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  session_id      uuid NOT NULL,
  business_date   date NOT NULL,
  snapshot_no     integer NOT NULL CHECK (snapshot_no >= 1),
  previous_snapshot_id uuid,
  payload         jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  sha256          text NOT NULL,
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, session_id) REFERENCES bk_closing_sessions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, previous_snapshot_id) REFERENCES bk_closing_snapshots (organization_id, company_id, id),
  UNIQUE (session_id),
  UNIQUE (company_id, business_date, snapshot_no),
  UNIQUE (organization_id, company_id, id)
);

-- the hash is computed by the database from the stored payload, so it can be re-checked at any time
CREATE FUNCTION app.bk_snapshot_hash() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.sha256 := encode(sha256(convert_to(NEW.payload::text, 'UTF8')), 'hex');
  RETURN NEW;
END $$;
CREATE TRIGGER bk_snapshots_hash BEFORE INSERT ON bk_closing_snapshots FOR EACH ROW EXECUTE FUNCTION app.bk_snapshot_hash();
CREATE TRIGGER bk_snapshots_immutable BEFORE UPDATE OR DELETE ON bk_closing_snapshots FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();
CREATE TRIGGER bk_approvals_immutable BEFORE UPDATE OR DELETE ON bk_closing_approvals FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();
CREATE TRIGGER bk_validation_runs_immutable BEFORE UPDATE OR DELETE ON bk_validation_runs FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();

CREATE TABLE bk_closing_exports (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  session_id      uuid NOT NULL,
  snapshot_id     uuid,
  export_id       uuid NOT NULL,
  created_by      uuid REFERENCES users (id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  FOREIGN KEY (organization_id, company_id, session_id) REFERENCES bk_closing_sessions (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, snapshot_id) REFERENCES bk_closing_snapshots (organization_id, company_id, id),
  FOREIGN KEY (organization_id, company_id, export_id) REFERENCES xl_exports (organization_id, company_id, id),
  UNIQUE (export_id)
);
CREATE INDEX bk_closing_exports_by_session ON bk_closing_exports (session_id);

-- what a client says about its local synchronisation state for a day. It is a DECLARATION by the client (the full sync engine
-- is a later phase); the validation shows it as such and never treats it as proof.
CREATE TABLE bk_sync_reports (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  client_id       text NOT NULL CHECK (length(client_id) BETWEEN 1 AND 80),
  business_date   date NOT NULL,
  pending_uploads integer NOT NULL DEFAULT 0 CHECK (pending_uploads >= 0),
  failed_uploads  integer NOT NULL DEFAULT 0 CHECK (failed_uploads >= 0),
  pending_changes integer NOT NULL DEFAULT 0 CHECK (pending_changes >= 0),
  conflicts       integer NOT NULL DEFAULT 0 CHECK (conflicts >= 0),
  last_sync_at    timestamptz,
  reported_by     uuid REFERENCES users (id),
  reported_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (company_id, client_id, business_date)
);

CREATE TABLE bk_audit_events (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  entity_type     text NOT NULL,
  entity_id       uuid,
  business_date   date,
  action          text NOT NULL,
  actor_type      text NOT NULL DEFAULT 'user' CHECK (actor_type IN ('user', 'system', 'ai')),
  actor_id        uuid REFERENCES users (id),
  reason          text CHECK (reason IS NULL OR length(reason) <= 500),
  summary         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id)
);
CREATE INDEX bk_audit_events_by_entity ON bk_audit_events (company_id, entity_type, entity_id, created_at);
CREATE INDEX bk_audit_events_by_date ON bk_audit_events (company_id, business_date, created_at);
CREATE TRIGGER bk_audit_immutable BEFORE UPDATE OR DELETE ON bk_audit_events FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();

-- ------------------------------------------------------------------ guards that protect a closed day
-- tasks: the status graph is fixed here as well as in the application
CREATE FUNCTION app.bk_guard_task() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'tasks are cancelled, never deleted' USING ERRCODE = 'integrity_constraint_violation'; END IF;
  IF NEW.status <> OLD.status AND NOT ((OLD.status, NEW.status) IN (
      ('new', 'assigned'), ('new', 'in_progress'), ('new', 'cancelled'),
      ('assigned', 'in_progress'), ('assigned', 'awaiting_information'), ('assigned', 'cancelled'), ('assigned', 'new'),
      ('in_progress', 'awaiting_information'), ('in_progress', 'awaiting_review'), ('in_progress', 'completed'), ('in_progress', 'cancelled'),
      ('awaiting_information', 'in_progress'), ('awaiting_information', 'cancelled'),
      ('awaiting_review', 'approved'), ('awaiting_review', 'rejected'),
      ('rejected', 'in_progress'), ('rejected', 'cancelled'),
      ('approved', 'completed'))) THEN
    RAISE EXCEPTION 'a task cannot go from % to %', OLD.status, NEW.status USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.status IN ('completed', 'cancelled') THEN
    RAISE EXCEPTION 'a % task is closed', OLD.status USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER bk_tasks_guard BEFORE UPDATE OR DELETE ON bk_tasks FOR EACH ROW EXECUTE FUNCTION app.bk_guard_task();

CREATE FUNCTION app.bk_guard_closed_day() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d date; c uuid;
BEGIN
  IF TG_OP = 'DELETE' THEN d := OLD.business_date; c := OLD.company_id; ELSE d := NEW.business_date; c := NEW.company_id; END IF;
  IF app.bk_day_closed(c, d) THEN
    RAISE EXCEPTION 'the day % is closed; reopen it first', d USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER bk_exceptions_closed_day BEFORE INSERT OR UPDATE OR DELETE ON bk_exceptions FOR EACH ROW EXECUTE FUNCTION app.bk_guard_closed_day();
CREATE TRIGGER bk_completions_closed_day BEFORE INSERT OR UPDATE OR DELETE ON bk_checklist_completions FOR EACH ROW EXECUTE FUNCTION app.bk_guard_closed_day();

-- an accounting transaction dated on a closed day cannot be approved (or posted) until the day is reopened.
-- Corrections to approved records stay possible through reversal, which is dated on its own day and is audited.
CREATE FUNCTION app.bk_guard_transaction_day() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status IN ('approved', 'posted') AND (TG_OP = 'INSERT' OR OLD.status NOT IN ('approved', 'posted', 'reversed'))
     AND NEW.reversal_of_id IS NULL AND app.bk_day_closed(NEW.company_id, NEW.transaction_date) THEN
    RAISE EXCEPTION 'the day % is closed; reopen it before approving transactions dated on it', NEW.transaction_date
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER acct_transactions_closed_day BEFORE INSERT OR UPDATE ON acct_transactions FOR EACH ROW EXECUTE FUNCTION app.bk_guard_transaction_day();

-- ------------------------------------------------------------------ row-level security
DO $$
DECLARE
  t text;
  scope text := 'organization_id = app.org_id() AND (app.company_scope() = ''all'' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), ''{}''::uuid[])))';
BEGIN
  FOREACH t IN ARRAY ARRAY['bk_policies', 'bk_checklists', 'bk_checklist_items', 'bk_checklist_completions', 'bk_tasks', 'bk_task_assignments',
      'bk_task_comments', 'bk_closing_sessions', 'bk_validation_runs', 'bk_exceptions', 'bk_closing_approvals', 'bk_closing_snapshots',
      'bk_closing_exports', 'bk_sync_reports', 'bk_audit_events'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO artify_app USING (%s)', t || '_select', t, scope);
    EXECUTE format('CREATE POLICY %I ON %I FOR INSERT TO artify_app WITH CHECK (%s)', t || '_insert', t, scope);
    IF t IN ('bk_policies', 'bk_checklists', 'bk_checklist_items', 'bk_checklist_completions', 'bk_tasks', 'bk_closing_sessions',
             'bk_exceptions', 'bk_sync_reports') THEN
      EXECUTE format('CREATE POLICY %I ON %I FOR UPDATE TO artify_app USING (%s) WITH CHECK (%s)', t || '_update', t, scope, scope);
    END IF;
    IF t = 'bk_checklist_completions' THEN
      EXECUTE format('CREATE POLICY %I ON %I FOR DELETE TO artify_app USING (%s)', t || '_delete', t, scope);
    END IF;
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON bk_policies, bk_checklists, bk_checklist_items, bk_tasks, bk_closing_sessions, bk_exceptions, bk_sync_reports TO artify_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON bk_checklist_completions TO artify_app;     -- un-ticking; blocked on a closed day by trigger
GRANT SELECT, INSERT ON bk_task_assignments, bk_task_comments, bk_validation_runs, bk_closing_approvals, bk_closing_snapshots,
  bk_closing_exports, bk_audit_events TO artify_app;                                -- append-only
-- no DELETE anywhere else: archive or cancel instead
