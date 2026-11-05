-- Phase 12 (continued): notification delivery. Outbox events become per-person notification rows (in-app) and, when the
-- person allows it, an e-mail. Rows hold only generic text plus an entity reference - never document names, amounts or
-- other business data - so a notification can be shown or mailed without leaking what the record contains.

CREATE TABLE notifications (
  id              uuid PRIMARY KEY,
  organization_id uuid NOT NULL REFERENCES organizations (id),
  company_id      uuid,
  user_id         uuid NOT NULL REFERENCES users (id),
  event_id        uuid NOT NULL,
  event_type      text NOT NULL CHECK (length(event_type) BETWEEN 1 AND 80),
  title           text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  body            text NOT NULL DEFAULT '' CHECK (length(body) <= 500),
  entity_type     text,
  entity_id       text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  read_at         timestamptz,
  email_state     text NOT NULL DEFAULT 'not_requested' CHECK (email_state IN ('not_requested', 'sent', 'failed', 'skipped')),
  email_error     text CHECK (email_error IS NULL OR length(email_error) <= 200),
  FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id),
  UNIQUE (event_id, user_id)
);
CREATE INDEX notifications_user ON notifications (user_id, organization_id, created_at DESC);
CREATE INDEX notifications_unread ON notifications (user_id, organization_id) WHERE read_at IS NULL;

CREATE TABLE notification_prefs (
  user_id       uuid PRIMARY KEY REFERENCES users (id),
  email_enabled boolean NOT NULL DEFAULT true,
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- A person may only mark their own notification read; delivery fields belong to the system context (no signed-in user).
CREATE FUNCTION app.notifications_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'notifications are kept' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.id <> OLD.id OR NEW.user_id <> OLD.user_id OR NEW.organization_id <> OLD.organization_id OR NEW.event_id <> OLD.event_id
     OR NEW.event_type <> OLD.event_type OR NEW.title <> OLD.title OR NEW.body <> OLD.body
     OR NEW.entity_id IS DISTINCT FROM OLD.entity_id OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'a notification cannot be rewritten' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF app.user_id() IS NOT NULL AND (NEW.email_state <> OLD.email_state OR NEW.email_error IS DISTINCT FROM OLD.email_error) THEN
    RAISE EXCEPTION 'delivery state is set by the system' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.read_at IS NOT NULL AND NEW.read_at IS DISTINCT FROM OLD.read_at THEN
    RAISE EXCEPTION 'a notification stays read' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER notifications_guard BEFORE UPDATE OR DELETE ON notifications FOR EACH ROW EXECUTE FUNCTION app.notifications_guard();

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications FORCE ROW LEVEL SECURITY;
CREATE POLICY notifications_own_select ON notifications FOR SELECT TO artify_app
  USING (user_id = app.user_id() AND organization_id = app.org_id());
CREATE POLICY notifications_system_select ON notifications FOR SELECT TO artify_app
  USING (app.user_id() IS NULL AND organization_id = app.org_id());
CREATE POLICY notifications_system_insert ON notifications FOR INSERT TO artify_app
  WITH CHECK (app.user_id() IS NULL AND organization_id = app.org_id());
CREATE POLICY notifications_own_update ON notifications FOR UPDATE TO artify_app
  USING (user_id = app.user_id() AND organization_id = app.org_id()) WITH CHECK (user_id = app.user_id() AND organization_id = app.org_id());
CREATE POLICY notifications_system_update ON notifications FOR UPDATE TO artify_app
  USING (app.user_id() IS NULL AND organization_id = app.org_id()) WITH CHECK (app.user_id() IS NULL AND organization_id = app.org_id());

ALTER TABLE notification_prefs ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_prefs FORCE ROW LEVEL SECURITY;
CREATE POLICY prefs_own ON notification_prefs FOR ALL TO artify_app
  USING (user_id = app.user_id()) WITH CHECK (user_id = app.user_id());
CREATE POLICY prefs_system_read ON notification_prefs FOR SELECT TO artify_app
  USING (app.user_id() IS NULL AND app.org_id() IS NOT NULL);

GRANT SELECT, INSERT, UPDATE ON notifications TO artify_app;
GRANT SELECT, INSERT, UPDATE ON notification_prefs TO artify_app;
