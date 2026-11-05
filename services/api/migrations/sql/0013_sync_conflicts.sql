-- Phase 12 (continued): server-recorded synchronization conflicts and their audited resolution.
-- A conflict is a client write based on an older revision (stale) or aimed at a locked record (locked). The row keeps who
-- found it, what the server held, who decided and why. Resolved rows can no longer change; nothing is ever deleted.

CREATE TABLE sync_conflicts (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  company_id      uuid NOT NULL,
  item_kind       text NOT NULL CHECK (item_kind IN ('workspace', 'document', 'transaction', 'closing', 'export')),
  item_id         text NOT NULL CHECK (length(item_id) BETWEEN 1 AND 64),
  client_id       text NOT NULL CHECK (length(client_id) BETWEEN 1 AND 80),
  conflict_type   text NOT NULL CHECK (conflict_type IN ('stale', 'locked')),
  base_revision   integer CHECK (base_revision IS NULL OR base_revision >= 0),
  server_revision integer,
  local_summary   jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(local_summary) = 'object' AND length(local_summary::text) <= 4096),
  server_summary  jsonb NOT NULL DEFAULT '{}'::jsonb,
  state           text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'resolved')),
  resolution      text CHECK (resolution IN ('keep_server', 'apply_local', 'merged', 'discarded')),
  resolution_note text CHECK (resolution_note IS NULL OR length(resolution_note) <= 500),
  detected_by     uuid NOT NULL REFERENCES users (id),
  detected_at     timestamptz NOT NULL DEFAULT now(),
  resolved_by     uuid REFERENCES users (id),
  resolved_at     timestamptz,
  row_version     integer NOT NULL DEFAULT 1,
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  CHECK ((state = 'open' AND resolution IS NULL AND resolved_by IS NULL AND resolved_at IS NULL)
      OR (state = 'resolved' AND resolution IS NOT NULL AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)),
  CHECK (resolution IS DISTINCT FROM 'apply_local' OR conflict_type = 'stale'),
  CHECK (resolution IS DISTINCT FROM 'merged' OR conflict_type = 'stale')
);
-- one open conflict per item and device: reporting twice is idempotent
CREATE UNIQUE INDEX sync_conflicts_one_open ON sync_conflicts (company_id, item_kind, item_id, client_id) WHERE state = 'open';
CREATE INDEX sync_conflicts_company_state ON sync_conflicts (company_id, state, detected_at DESC);

CREATE FUNCTION app.sync_conflict_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'sync conflicts are kept for the audit trail' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.state = 'resolved' THEN
    RAISE EXCEPTION 'a resolved conflict cannot change' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.item_kind <> OLD.item_kind OR NEW.item_id <> OLD.item_id OR NEW.client_id <> OLD.client_id
     OR NEW.company_id <> OLD.company_id OR NEW.detected_by <> OLD.detected_by OR NEW.conflict_type <> OLD.conflict_type THEN
    RAISE EXCEPTION 'what a conflict is about cannot change' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER sync_conflicts_guard BEFORE UPDATE OR DELETE ON sync_conflicts FOR EACH ROW EXECUTE FUNCTION app.sync_conflict_guard();

ALTER TABLE sync_conflicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_conflicts FORCE ROW LEVEL SECURITY;
CREATE POLICY sync_conflicts_select ON sync_conflicts FOR SELECT TO artify_app
  USING (organization_id = app.org_id() AND (app.company_scope() = 'all' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), '{}'::uuid[]))));
CREATE POLICY sync_conflicts_insert ON sync_conflicts FOR INSERT TO artify_app
  WITH CHECK (organization_id = app.org_id() AND (app.company_scope() = 'all' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), '{}'::uuid[]))));
CREATE POLICY sync_conflicts_update ON sync_conflicts FOR UPDATE TO artify_app
  USING (organization_id = app.org_id() AND (app.company_scope() = 'all' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), '{}'::uuid[]))))
  WITH CHECK (organization_id = app.org_id() AND (app.company_scope() = 'all' OR company_id = ANY (COALESCE((SELECT app.visible_company_ids()), '{}'::uuid[]))));
GRANT SELECT, INSERT, UPDATE ON sync_conflicts TO artify_app;
