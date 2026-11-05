-- Phase 6 review fix: stored document binaries must be immutable in the database, not only in the application code.
-- Phase 6 granted UPDATE on document_versions and storage_objects to the application role without any guard, so a bug
-- (or an injected statement) could silently change the hash, size or storage key of an original (invariant 8).
-- The application only ever changes `storage_objects.state / verified_at / purged_at` and never updates a version row.

CREATE FUNCTION app.guard_document_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'document versions are never deleted' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  -- only the scan result may change after a version was stored
  IF (NEW.id, NEW.organization_id, NEW.company_id, NEW.document_id, NEW.version_no, NEW.original_filename, NEW.extension,
      NEW.mime_type, NEW.size_bytes, NEW.sha256, NEW.storage_object_id, NEW.uploaded_by, NEW.uploaded_at,
      NEW.change_reason, NEW.restored_from_version)
     IS DISTINCT FROM
     (OLD.id, OLD.organization_id, OLD.company_id, OLD.document_id, OLD.version_no, OLD.original_filename, OLD.extension,
      OLD.mime_type, OLD.size_bytes, OLD.sha256, OLD.storage_object_id, OLD.uploaded_by, OLD.uploaded_at,
      OLD.change_reason, OLD.restored_from_version) THEN
    RAISE EXCEPTION 'a stored document version is immutable' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER document_versions_guard BEFORE UPDATE OR DELETE ON document_versions
  FOR EACH ROW EXECUTE FUNCTION app.guard_document_version();

CREATE FUNCTION app.guard_storage_object() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'storage object records are never deleted' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  -- identity, location, size and hash never change; only the lifecycle columns may
  IF (NEW.id, NEW.organization_id, NEW.company_id, NEW.backend, NEW.storage_key, NEW.size_bytes, NEW.sha256, NEW.created_at)
     IS DISTINCT FROM
     (OLD.id, OLD.organization_id, OLD.company_id, OLD.backend, OLD.storage_key, OLD.size_bytes, OLD.sha256, OLD.created_at) THEN
    RAISE EXCEPTION 'a stored object record is immutable' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.state = 'purged' AND NEW.state <> 'purged' THEN
    RAISE EXCEPTION 'a purged object stays purged' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER storage_objects_guard BEFORE UPDATE OR DELETE ON storage_objects
  FOR EACH ROW EXECUTE FUNCTION app.guard_storage_object();
