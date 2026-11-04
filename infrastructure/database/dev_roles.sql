-- DEVELOPMENT / CI ONLY. Creates login roles with throwaway passwords as members of the
-- NOLOGIN group roles created by the migrations. Production credentials come from the
-- secret manager and are never stored in this repository.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'artify_app_login') THEN
    CREATE ROLE artify_app_login LOGIN PASSWORD 'dev-only-app' IN ROLE artify_app;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'artify_platform_login') THEN
    CREATE ROLE artify_platform_login LOGIN PASSWORD 'dev-only-platform' IN ROLE artify_platform;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'artify_dispatcher_login') THEN
    CREATE ROLE artify_dispatcher_login LOGIN PASSWORD 'dev-only-dispatcher' IN ROLE artify_dispatcher;
  END IF;
END $$;
