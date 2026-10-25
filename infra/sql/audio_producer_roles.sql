-- OPERATOR ONLY, after migrations in the target database. No credentials or
-- runtime membership grants. Create isolated LOGIN members externally for tests.
-- Requires trusted migration administrator; never execute in app startup.
\set ON_ERROR_STOP on
DO $$BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='ts_audio_owner') THEN CREATE ROLE ts_audio_owner NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='ts_audio_operator') THEN CREATE ROLE ts_audio_operator NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='ts_audio_publisher') THEN CREATE ROLE ts_audio_publisher NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='ts_audio_reader') THEN CREATE ROLE ts_audio_reader NOLOGIN; END IF;
END$$;
DO $$DECLARE r record; BEGIN
 FOR r IN SELECT * FROM pg_roles WHERE rolname IN ('ts_audio_owner','ts_audio_operator','ts_audio_publisher','ts_audio_reader') LOOP
  IF r.rolcanlogin OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls THEN RAISE EXCEPTION 'unsafe preexisting ledger role %',r.rolname; END IF;
 END LOOP;
 IF pg_has_role('ts_audio_operator','ts_audio_owner','MEMBER') OR pg_has_role('ts_audio_publisher','ts_audio_owner','MEMBER') OR pg_has_role('ts_audio_reader','ts_audio_owner','MEMBER')
  OR pg_has_role('ts_audio_operator','ts_audio_publisher','MEMBER') OR pg_has_role('ts_audio_publisher','ts_audio_operator','MEMBER')
  OR pg_has_role('ts_audio_reader','ts_audio_operator','MEMBER') OR pg_has_role('ts_audio_reader','ts_audio_publisher','MEMBER') THEN RAISE EXCEPTION 'unsafe cross-purpose membership'; END IF;
END$$;
-- Existing application schema privileges are not changed. All definer names
-- are qualified; search paths contain only pg_catalog,pg_temp.
DO $$DECLARE t text; f record; BEGIN
 FOREACH t IN ARRAY ARRAY['authority','execution','attempt','receipt','control','event'] LOOP
  EXECUTE format('ALTER TABLE public.%I OWNER TO ts_audio_owner','audio_producer_'||t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,ts_audio_operator,ts_audio_publisher,ts_audio_reader','audio_producer_'||t);
  EXECUTE format('GRANT SELECT ON public.%I TO ts_audio_reader,ts_audio_operator,ts_audio_publisher','audio_producer_'||t);
 END LOOP;
 FOR f IN SELECT oid::regprocedure AS signature FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname LIKE 'audio_p1_%' LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO ts_audio_owner',f.signature);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
 END LOOP;
END$$;
GRANT USAGE ON SCHEMA public TO ts_audio_owner,ts_audio_operator,ts_audio_publisher,ts_audio_reader;
GRANT INSERT ON public.audio_producer_authority TO ts_audio_operator;
GRANT INSERT ON public.audio_producer_execution,public.audio_producer_attempt,public.audio_producer_receipt TO ts_audio_publisher;
GRANT EXECUTE ON FUNCTION public.audio_p1_create_control(uuid,text,uuid),public.audio_p1_transition(uuid,bigint,uuid,text,text) TO ts_audio_operator,ts_audio_publisher;
GRANT EXECUTE ON FUNCTION public.audio_p1_lock_controls(uuid[],bigint[],bigint,bigint) TO ts_audio_reader;
