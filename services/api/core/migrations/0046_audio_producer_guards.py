"""Inactive ledger SQL guards. Operator role ownership is separately provisioned."""

from django.db import migrations

SQL = r"""
-- Only the new integer/string speech projection. Not a replacement v1 JSON
-- serializer: proof v1 numeric/cue validation stays in the pure publisher.
CREATE FUNCTION public.audio_p1_speech_json(v jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
DECLARE result text;
BEGIN
 CASE jsonb_typeof(v)
 WHEN 'object' THEN SELECT '{'||coalesce(string_agg(to_json(key)::text||':'||public.audio_p1_speech_json(value),',' ORDER BY key COLLATE "C"),'')||'}' INTO result FROM jsonb_each(v);
 WHEN 'array' THEN SELECT '['||coalesce(string_agg(public.audio_p1_speech_json(value),',' ORDER BY n),'')||']' INTO result FROM jsonb_array_elements(v) WITH ORDINALITY a(value,n);
 WHEN 'number' THEN IF v::text !~ '^-?(0|[1-9][0-9]*)$' THEN RAISE EXCEPTION 'integer speech units required'; END IF; result:=v::text;
 WHEN 'string','boolean','null' THEN result:=v::text;
 ELSE RAISE EXCEPTION 'missing speech projection';
 END CASE;
 RETURN result;
END $$;
CREATE FUNCTION public.audio_p1_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='TRUNCATE' AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=session_user AND rolsuper) THEN RETURN NULL; END IF;
 RAISE EXCEPTION 'immutable producer history' USING ERRCODE='42501'; END $$;
CREATE FUNCTION public.audio_p1_control_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR current_user <> pg_catalog.pg_get_userbyid((SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID)) THEN
  RAISE EXCEPTION 'control writes require authority routine' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.audio_p1_insert_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE ex public.audio_producer_execution; ev public.audio_producer_event; ac public.audio_producer_control;
        at public.audio_producer_attempt; au public.audio_producer_authority;
BEGIN
 IF NEW.payload->>'schema_version' IS DISTINCT FROM CASE TG_TABLE_NAME WHEN 'audio_producer_authority' THEN 'production-producer-enrollment.v1' WHEN 'audio_producer_execution' THEN 'production-producer-execution.v1' WHEN 'audio_producer_attempt' THEN 'production-tts-observation.v1' WHEN 'audio_producer_receipt' THEN 'production-audio-receipt.v1' ELSE 'production-producer-control-event.v1' END THEN RAISE EXCEPTION 'unsupported payload schema'; END IF;
 IF NEW.canonical_payload::jsonb <> NEW.payload OR octet_length(NEW.canonical_payload)>1048576 THEN
  RAISE EXCEPTION 'canonical payload mismatch';
 END IF;
 IF NEW.payload_hash IS DISTINCT FROM 'sha256:'||encode(sha256(convert_to('visual-design-hash.v1'||chr(10)||(NEW.payload->>'schema_version')||chr(10)||NEW.canonical_payload,'UTF8')),'hex') THEN RAISE EXCEPTION 'payload digest mismatch'; END IF;
 IF TG_TABLE_NAME='audio_producer_authority' THEN
  IF NEW.payload->>'enrollment_id' IS DISTINCT FROM NEW.id::text
   OR NEW.payload->>'issuer_id' IS DISTINCT FROM NEW.issuer_id OR NEW.payload->>'key_id' IS DISTINCT FROM NEW.key_id
   OR NEW.payload->>'public_key' IS DISTINCT FROM NEW.public_key OR NEW.payload->>'environment' IS DISTINCT FROM NEW.environment
   OR NEW.payload->>'image_hash' IS DISTINCT FROM NEW.image_hash OR NEW.payload->>'profile_hash' IS DISTINCT FROM NEW.profile_hash
   OR NEW.payload#>>'{window,issued_ms}' IS DISTINCT FROM NEW.not_before_ms::text OR NEW.payload#>>'{window,expires_ms}' IS DISTINCT FROM NEW.expires_ms::text
   OR NOT (NEW.payload->'purposes' @> jsonb_build_array(NEW.purpose))
   OR NEW.purpose NOT IN ('operator.enrollment.v1','operator.control.v1','publisher.context.v1','publisher.attempt.v1','publisher.original-receipt.v1','publisher.reuse.v1','tts.original-observation.v1') THEN RAISE EXCEPTION 'authority payload mismatch'; END IF;
  -- Enrollment is operator-only; key-family separation is still enforced here.
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.public_key,0));
  IF EXISTS(SELECT 1 FROM public.audio_producer_authority WHERE public_key=NEW.public_key AND split_part(purpose,'.',1)<>split_part(NEW.purpose,'.',1)) THEN RAISE EXCEPTION 'cross-purpose signing key'; END IF;
 ELSIF TG_TABLE_NAME='audio_producer_execution' THEN
  IF NEW.payload->>'execution_id' IS DISTINCT FROM NEW.id::text OR NEW.payload#>>'{scope,owner_id}' IS DISTINCT FROM NEW.owner_id::text
   OR NEW.payload#>>'{scope,project_id}' IS DISTINCT FROM NEW.project_id::text
   OR NEW.payload->>'job_id' IS DISTINCT FROM NEW.job_id::text
   OR NEW.payload#>>'{scope,page,page_key}' IS DISTINCT FROM NEW.page_key
   OR NEW.payload#>>'{scope,page,source_slide_index}' IS DISTINCT FROM NEW.source_slide_index::text
   OR NEW.payload#>>'{scope,page,split_index}' IS DISTINCT FROM NEW.split_index::text
   OR NEW.owner_id<1 OR NEW.project_id<1 OR NEW.job_id<1 THEN RAISE EXCEPTION 'execution scope mismatch'; END IF;
  IF NEW.planned_speech_hash IS DISTINCT FROM 'sha256:'||encode(sha256(convert_to('visual-design-hash.v1'||chr(10)||'production-speech-semantics.v1'||chr(10)||public.audio_p1_speech_json(NEW.payload->'speech'),'UTF8')),'hex') THEN RAISE EXCEPTION 'planned speech mismatch'; END IF;
  SELECT * INTO STRICT au FROM public.audio_producer_authority WHERE id=NEW.issuer_id;
  IF au.purpose<>'publisher.context.v1' THEN RAISE EXCEPTION 'wrong context issuer'; END IF;
 ELSIF TG_TABLE_NAME='audio_producer_attempt' THEN
  SELECT * INTO STRICT ex FROM public.audio_producer_execution WHERE id=NEW.execution_id;
  SELECT * INTO STRICT ev FROM public.audio_producer_event WHERE id=NEW.issued_event_id;
  SELECT * INTO STRICT au FROM public.audio_producer_authority WHERE id=NEW.issuer_id;
  -- Authorities precede execution controls. No file/network work under locks.
  PERFORM 1 FROM public.audio_producer_control WHERE authority_id IN (ex.issuer_id,NEW.issuer_id) ORDER BY authority_id FOR UPDATE;
  IF (SELECT count(*) FROM public.audio_producer_control c JOIN public.audio_producer_authority a ON a.id=c.authority_id WHERE c.authority_id IN (ex.issuer_id,NEW.issuer_id) AND c.status='active' AND a.not_before_ms<=extract(epoch FROM clock_timestamp())*1000 AND a.expires_ms>extract(epoch FROM clock_timestamp())*1000)<>2 THEN RAISE EXCEPTION 'issuer unavailable'; END IF;
  IF au.environment IS DISTINCT FROM (SELECT environment FROM public.audio_producer_authority WHERE id=ex.issuer_id) OR au.profile_hash IS DISTINCT FROM (SELECT profile_hash FROM public.audio_producer_authority WHERE id=ex.issuer_id) THEN RAISE EXCEPTION 'issuer profile mismatch'; END IF;
  SELECT * INTO STRICT ac FROM public.audio_producer_control WHERE execution_id=ex.id FOR UPDATE;
  IF NEW.outcome='complete' AND (NEW.payload->'actual_speech' IS DISTINCT FROM ex.payload->'speech') THEN RAISE EXCEPTION 'actual plan drift'; END IF;
  IF au.purpose<>'tts.original-observation.v1' OR ev.kind<>'attempt_issued' OR ev.control_id<>ac.id
   OR ev.execution_id<>ex.id OR ev.generation<>NEW.generation OR ev.issued_nonce<>NEW.nonce OR ev.context_hash<>NEW.context_hash
   OR ac.status<>'active' OR ac.generation<>NEW.generation OR ac.nonce IS DISTINCT FROM NEW.nonce OR ac.deadline IS NULL OR ac.deadline<=clock_timestamp()
   OR NEW.payload->>'execution_id' IS DISTINCT FROM NEW.execution_id::text
   OR NEW.payload->>'observation_id' IS DISTINCT FROM NEW.id::text
   OR NEW.payload->>'nonce' IS DISTINCT FROM NEW.nonce::text
   OR NEW.payload->>'context_hash' IS DISTINCT FROM NEW.context_hash
   OR NEW.payload->>'generation' IS DISTINCT FROM NEW.generation::text
   OR NEW.payload->>'outcome' IS DISTINCT FROM NEW.outcome THEN RAISE EXCEPTION 'unissued/stale observation'; END IF;
  IF NEW.actual_speech_hash IS DISTINCT FROM CASE WHEN NEW.payload->'actual_speech'='null'::jsonb THEN NULL ELSE 'sha256:'||encode(sha256(convert_to('visual-design-hash.v1'||chr(10)||'production-speech-semantics.v1'||chr(10)||public.audio_p1_speech_json(NEW.payload->'actual_speech'),'UTF8')),'hex') END THEN RAISE EXCEPTION 'actual speech mismatch'; END IF;
 ELSIF TG_TABLE_NAME='audio_producer_receipt' THEN
  SELECT * INTO STRICT at FROM public.audio_producer_attempt WHERE id=NEW.attempt_id;
  SELECT * INTO STRICT ex FROM public.audio_producer_execution WHERE id=NEW.execution_id;
  SELECT * INTO STRICT au FROM public.audio_producer_authority WHERE id=NEW.issuer_id;
  PERFORM 1 FROM public.audio_producer_control WHERE authority_id IN (ex.issuer_id,at.issuer_id,NEW.issuer_id) ORDER BY authority_id FOR UPDATE;
  IF EXISTS(SELECT 1 FROM public.audio_producer_control WHERE authority_id IN (ex.issuer_id,at.issuer_id,NEW.issuer_id) AND status<>'active')
   OR (SELECT count(*) FROM public.audio_producer_control WHERE authority_id IN (ex.issuer_id,at.issuer_id,NEW.issuer_id))<>(SELECT count(DISTINCT v) FROM unnest(ARRAY[ex.issuer_id,at.issuer_id,NEW.issuer_id]) v) THEN RAISE EXCEPTION 'issuer unavailable'; END IF;
  SELECT * INTO STRICT ac FROM public.audio_producer_control WHERE execution_id=ex.id FOR UPDATE;
  IF EXISTS(SELECT 1 FROM public.audio_producer_authority WHERE id IN (ex.issuer_id,at.issuer_id,NEW.issuer_id) AND (environment<>au.environment OR profile_hash<>au.profile_hash OR not_before_ms>extract(epoch FROM clock_timestamp())*1000 OR expires_ms<=extract(epoch FROM clock_timestamp())*1000)) THEN RAISE EXCEPTION 'issuer profile/expiry mismatch'; END IF;
  IF au.purpose<>'publisher.original-receipt.v1' OR at.execution_id<>ex.id OR at.outcome<>'complete'
   OR ac.status<>'active' OR ac.generation<>at.generation OR ac.nonce IS DISTINCT FROM at.nonce OR ac.accepted_receipt_id IS NOT NULL
   OR NEW.payload->>'receipt_id' IS DISTINCT FROM NEW.id::text
   OR NEW.payload#>>'{proof,owner_id}' IS DISTINCT FROM ex.owner_id::text
   OR NEW.payload#>>'{proof,project_id}' IS DISTINCT FROM ex.project_id::text
   OR NEW.payload#>'{proof,page}' IS DISTINCT FROM ex.payload#>'{scope,page}'
   OR NEW.payload#>>'{proof,baseline,job_id}' IS DISTINCT FROM ex.job_id::text
   OR NEW.payload#>>'{proof,baseline,run_id}' IS DISTINCT FROM ex.id::text
   OR NEW.payload->>'proof_hash' IS DISTINCT FROM NEW.proof_hash
   OR NEW.payload->>'context_hash' IS DISTINCT FROM at.context_hash
   OR NEW.payload->>'speech_hash' IS DISTINCT FROM at.actual_speech_hash
   OR NEW.payload->>'observation_hash' IS DISTINCT FROM at.payload_hash THEN RAISE EXCEPTION 'receipt parent/scope mismatch'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.audio_p1_create_control(cid uuid, target_kind text, target_id uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF (target_kind='authority' AND NOT pg_has_role(session_user,'ts_audio_operator','MEMBER'))
 OR (target_kind IN ('execution','receipt') AND NOT pg_has_role(session_user,'ts_audio_publisher','MEMBER'))
 OR target_kind NOT IN ('authority','execution','receipt') THEN RAISE EXCEPTION 'wrong purpose' USING ERRCODE='42501'; END IF;
 INSERT INTO public.audio_producer_control(id,authority_id,execution_id,receipt_id,epoch,status,generation)
 VALUES(cid,CASE WHEN target_kind='authority' THEN target_id END,CASE WHEN target_kind='execution' THEN target_id END,
 CASE WHEN target_kind='receipt' THEN target_id END,0,'disabled',0);
END $$;
CREATE FUNCTION public.audio_p1_transition(cid uuid, expected_epoch bigint, eid uuid, action text, data text) RETURNS bigint
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE c public.audio_producer_control; p jsonb:=data::jsonb; prior text; au uuid; r public.audio_producer_receipt; event_data text;
BEGIN
 IF octet_length(data)>1048576 THEN RAISE EXCEPTION 'event too large'; END IF;
 IF p->>'schema_version' IS DISTINCT FROM 'production-producer-control.v1' OR p->>'control_id' IS DISTINCT FROM cid::text OR p->>'expected_epoch' IS DISTINCT FROM expected_epoch::text OR p->>'action' IS DISTINCT FROM action
  OR p#>>'{window,issued_ms}' IS NULL OR p#>>'{window,expires_ms}' IS NULL
  OR (p#>>'{window,issued_ms}')::bigint>extract(epoch FROM clock_timestamp())*1000 OR (p#>>'{window,expires_ms}')::bigint<=extract(epoch FROM clock_timestamp())*1000 THEN RAISE EXCEPTION 'control instruction mismatch/expiry'; END IF;
 SELECT * INTO STRICT c FROM public.audio_producer_control WHERE id=cid;
 IF c.authority_id IS NOT NULL THEN
  IF NOT pg_has_role(session_user,'ts_audio_operator','MEMBER') OR action NOT IN ('activate','revoke','retire') THEN RAISE EXCEPTION 'operator required' USING ERRCODE='42501'; END IF;
 ELSE
  IF NOT pg_has_role(session_user,'ts_audio_publisher','MEMBER') AND NOT (action='revoke' AND pg_has_role(session_user,'ts_audio_operator','MEMBER')) THEN RAISE EXCEPTION 'publisher required' USING ERRCODE='42501'; END IF;
  IF action NOT IN ('activate','revoke','attempt_issued','invalidate','accept') THEN RAISE EXCEPTION 'invalid action'; END IF;
  IF c.receipt_id IS NOT NULL AND action NOT IN ('activate','revoke') THEN RAISE EXCEPTION 'wrong receipt action'; END IF;
  SELECT issuer_id INTO au FROM public.audio_producer_execution WHERE id=c.execution_id;
  IF c.receipt_id IS NOT NULL THEN SELECT issuer_id INTO au FROM public.audio_producer_receipt WHERE id=c.receipt_id; END IF;
  PERFORM 1 FROM public.audio_producer_control WHERE authority_id=au FOR UPDATE;
  IF action<>'revoke' AND NOT EXISTS(SELECT 1 FROM public.audio_producer_control WHERE authority_id=au AND status='active' AND EXISTS(SELECT 1 FROM public.audio_producer_authority a WHERE a.id=au AND a.not_before_ms<=extract(epoch FROM clock_timestamp())*1000 AND a.expires_ms>extract(epoch FROM clock_timestamp())*1000)) THEN RAISE EXCEPTION 'authority denied'; END IF;
 END IF;
 SELECT * INTO STRICT c FROM public.audio_producer_control WHERE id=cid FOR UPDATE;
 IF c.epoch<>expected_epoch OR c.status='revoked' THEN RAISE EXCEPTION 'stale/revoked control'; END IF;
 SELECT payload_hash INTO prior FROM public.audio_producer_event WHERE id=c.head_event_id;
 IF action='attempt_issued' THEN
  IF NOT (p ?& ARRAY['generation','nonce','context_hash','expires_ms']) OR c.execution_id IS NULL OR c.status<>'active' OR c.accepted_receipt_id IS NOT NULL OR c.generation>=3
   OR (p->>'generation')::int<>c.generation+1 OR (p->>'expires_ms')::bigint<=extract(epoch FROM clock_timestamp())*1000
   OR (p->>'expires_ms')::bigint>extract(epoch FROM clock_timestamp())*1000+300000 THEN RAISE EXCEPTION 'invalid attempt grant'; END IF;
 END IF;
 IF action='accept' THEN
  SELECT * INTO STRICT r FROM public.audio_producer_receipt WHERE id=(p->>'receipt_id')::uuid;
  IF r.execution_id<>c.execution_id OR c.accepted_receipt_id IS NOT NULL THEN RAISE EXCEPTION 'wrong accepted receipt'; END IF;
 END IF;
 event_data:='{"control_id":'||to_json(cid::text)::text||',"instruction":'||data||',"kind":'||to_json(action)::text||',"previous_hash":'||coalesce(to_json(prior)::text,'null')||',"schema_version":"production-producer-control-event.v1","sequence":'||(c.epoch+1)::text||'}';
 INSERT INTO public.audio_producer_event(id,payload,payload_hash,canonical_payload,created_at,control_id,sequence,previous_hash,kind,execution_id,generation,issued_nonce,context_hash,instruction)
 VALUES(eid,event_data::jsonb,'sha256:'||encode(sha256(convert_to('visual-design-hash.v1'||chr(10)||'production-producer-control-event.v1'||chr(10)||event_data,'UTF8')),'hex'),event_data,clock_timestamp(),cid,c.epoch+1,prior,action,
 CASE WHEN action='attempt_issued' THEN c.execution_id END,CASE WHEN action='attempt_issued' THEN (p->>'generation')::int END,
 CASE WHEN action='attempt_issued' THEN (p->>'nonce')::uuid END,CASE WHEN action='attempt_issued' THEN p->>'context_hash' END,data);
 UPDATE public.audio_producer_control SET epoch=c.epoch+1,head_event_id=eid,
 status=CASE WHEN action='revoke' THEN 'revoked' WHEN action='retire' THEN 'retired_valid' ELSE 'active' END,
 generation=CASE WHEN action='attempt_issued' THEN (p->>'generation')::int ELSE c.generation END,
 nonce=CASE WHEN action='attempt_issued' THEN (p->>'nonce')::uuid WHEN action='invalidate' THEN NULL ELSE c.nonce END,
 deadline=CASE WHEN action='attempt_issued' THEN to_timestamp((p->>'expires_ms')::double precision/1000) WHEN action='invalidate' THEN NULL ELSE c.deadline END,
 accepted_receipt_id=CASE WHEN action='accept' THEN r.id ELSE c.accepted_receipt_id END WHERE id=cid;
 RETURN c.epoch+1;
END $$;
CREATE FUNCTION public.audio_p1_lock_controls(ids uuid[], epochs bigint[], owner_pk bigint, project_pk bigint) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE c public.audio_producer_control; n integer; ex public.audio_producer_execution;
BEGIN
 IF ids IS NULL OR epochs IS NULL OR array_position(ids,NULL) IS NOT NULL OR array_position(epochs,NULL) IS NOT NULL OR cardinality(ids) NOT BETWEEN 1 AND 32 OR cardinality(ids)<>cardinality(epochs)
 OR cardinality(ids)<>(SELECT count(DISTINCT v) FROM unnest(ids) v) THEN RAISE EXCEPTION 'bounded unique controls required'; END IF;
 FOR c IN SELECT * FROM public.audio_producer_control WHERE id=ANY(ids)
 ORDER BY CASE WHEN authority_id IS NOT NULL THEN 0 WHEN receipt_id IS NOT NULL THEN 1 ELSE 2 END,coalesce(authority_id,receipt_id,execution_id),id FOR SHARE LOOP
  n:=array_position(ids,c.id);
  IF c.execution_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.audio_producer_control a JOIN public.audio_producer_execution e ON e.issuer_id=a.authority_id WHERE e.id=c.execution_id AND a.id=ANY(ids)) THEN RAISE EXCEPTION 'missing parent authority'; END IF;
  IF c.receipt_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.audio_producer_receipt r JOIN public.audio_producer_attempt t ON t.id=r.attempt_id JOIN public.audio_producer_execution e ON e.id=r.execution_id CROSS JOIN LATERAL unnest(ARRAY[r.issuer_id,t.issuer_id,e.issuer_id]) wanted WHERE r.id=c.receipt_id AND NOT EXISTS(SELECT 1 FROM public.audio_producer_control a WHERE a.authority_id=wanted AND a.id=ANY(ids))) THEN RAISE EXCEPTION 'missing parent authority'; END IF;
  IF c.authority_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.audio_producer_authority a WHERE a.id=c.authority_id AND a.not_before_ms<=extract(epoch FROM clock_timestamp())*1000 AND a.expires_ms>extract(epoch FROM clock_timestamp())*1000) THEN RAISE EXCEPTION 'expired issuer'; END IF;
  IF c.epoch<>epochs[n] OR c.status NOT IN ('active','retired_valid') THEN RAISE EXCEPTION 'current control denied'; END IF;
  IF c.execution_id IS NOT NULL OR c.receipt_id IS NOT NULL THEN
   SELECT e.* INTO STRICT ex FROM public.audio_producer_execution e LEFT JOIN public.audio_producer_receipt r ON r.execution_id=e.id WHERE e.id=c.execution_id OR r.id=c.receipt_id;
   IF ex.owner_id<>owner_pk OR ex.project_id<>project_pk THEN RAISE EXCEPTION 'wrong control scope'; END IF;
  END IF;
 END LOOP;
 IF (SELECT count(*) FROM public.audio_producer_control WHERE id=ANY(ids))<>cardinality(ids) THEN RAISE EXCEPTION 'missing control'; END IF;
END $$;
"""
CHECK_SQL = r"""
CREATE FUNCTION public.audio_p1_receipt_commit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.audio_producer_control c JOIN public.audio_producer_event e ON e.id=c.head_event_id WHERE c.execution_id=NEW.execution_id AND c.accepted_receipt_id=NEW.id AND e.kind='accept' AND e.payload#>>'{instruction,receipt_id}'=NEW.id::text)
  OR NOT EXISTS(SELECT 1 FROM public.audio_producer_control WHERE receipt_id=NEW.id AND status='active' AND head_event_id IS NOT NULL) THEN RAISE EXCEPTION 'uncommitted receipt acceptance'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER audio_receipt_commit AFTER INSERT ON public.audio_producer_receipt DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.audio_p1_receipt_commit();
CREATE FUNCTION public.audio_p1_control_commit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c public.audio_producer_control;
BEGIN
 SELECT * INTO STRICT c FROM public.audio_producer_control WHERE id=NEW.id;
 IF c.epoch=0 THEN
  IF c.status<>'disabled' OR c.head_event_id IS NOT NULL OR c.generation<>0 OR c.accepted_receipt_id IS NOT NULL THEN RAISE EXCEPTION 'incomplete initial control'; END IF;
 ELSIF NOT EXISTS(SELECT 1 FROM public.audio_producer_event WHERE id=c.head_event_id AND control_id=c.id AND sequence=c.epoch) THEN RAISE EXCEPTION 'incomplete control event'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER audio_control_commit AFTER INSERT OR UPDATE ON public.audio_producer_control DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.audio_p1_control_commit();
"""
TABLES = ["authority", "execution", "attempt", "receipt", "event"]


def install(apps, editor):
    if editor.connection.vendor != "postgresql":
        return
    editor.execute(SQL)
    editor.execute(CHECK_SQL)
    for name in TABLES:
        table = "audio_producer_" + name
        editor.execute(
            f"CREATE TRIGGER audio_history_update BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.audio_p1_immutable()"
        )
        editor.execute(
            f"CREATE TRIGGER audio_history_truncate BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.audio_p1_immutable()"
        )
        editor.execute(
            f"CREATE TRIGGER audio_history_insert BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.audio_p1_insert_guard()"
        )
        editor.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
    editor.execute(
        "CREATE TRIGGER audio_control_mutation BEFORE INSERT OR UPDATE OR DELETE ON public.audio_producer_control FOR EACH ROW EXECUTE FUNCTION public.audio_p1_control_write()"
    )
    editor.execute(
        "CREATE TRIGGER audio_control_truncate BEFORE TRUNCATE ON public.audio_producer_control FOR EACH STATEMENT EXECUTE FUNCTION public.audio_p1_immutable()"
    )
    for signature in [
        "audio_p1_create_control(uuid,text,uuid)",
        "audio_p1_transition(uuid,bigint,uuid,text,text)",
        "audio_p1_lock_controls(uuid[],bigint[],bigint,bigint)",
    ]:
        editor.execute("REVOKE ALL ON FUNCTION public." + signature + " FROM PUBLIC")


class Migration(migrations.Migration):
    dependencies = [("core", "0045_inactive_audio_producer_ledger")]
    operations = [migrations.RunPython(install)]
