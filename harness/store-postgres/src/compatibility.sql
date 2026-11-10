-- Old clients ignore safety fields. Fence their writes even if they raced an
-- upgrade startup check. This is a compatibility boundary, not DB-user auth.
CREATE OR REPLACE FUNCTION ow_require_safety_runtime() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF current_setting('ow.runtime_schema',true) IS DISTINCT FROM '6' THEN
   RAISE EXCEPTION 'Runtime schema 6 required; upgrade the client before writing';
 END IF;
 RETURN NULL;
END $$;
DO $$ DECLARE name TEXT; BEGIN
 FOREACH name IN ARRAY ARRAY['sessions','events','attempts','history','submissions','controls'] LOOP
 EXECUTE format('DROP TRIGGER IF EXISTS ow_runtime_compatibility ON %I',name);
 EXECUTE format('CREATE TRIGGER ow_runtime_compatibility BEFORE INSERT OR UPDATE OR DELETE ON %I FOR EACH STATEMENT EXECUTE FUNCTION ow_require_safety_runtime()',name);
 END LOOP;
END $$;
ALTER TABLE submissions DROP CONSTRAINT IF EXISTS submissions_status_check;
ALTER TABLE submissions ADD CONSTRAINT submissions_status_check CHECK(status IN ('queued','started','completed','failed','cancelled','interrupted','truncated','blocked'));
