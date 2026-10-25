"""Mandatory P1 real-role tests. Admin provisions only; runtime assertions use
independent non-superuser LOGIN connections. No issuer/key is deployed.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
import uuid
import pytest
from django.db import connection
import psycopg2
from core.visual_design.schemas import canonical_json, content_hash

pytestmark = pytest.mark.django_db(transaction=True)
H = "sha256:" + "1" * 64
U = lambda: str(uuid.uuid4())


@pytest.fixture
def pg():
    if connection.vendor != "postgresql":
        if os.environ.get("P1_REQUIRE_POSTGRES") == "1":
            pytest.fail("Mandatory real PostgreSQL unavailable")
        pytest.skip(
            "real PostgreSQL role gate; required CI job uses P1_REQUIRE_POSTGRES=1"
        )
    sql = (Path(__file__).parents[2] / "infra/sql/audio_producer_roles.sql").read_text()
    sql = "\n".join(l for l in sql.splitlines() if not l.startswith("\\"))
    password = uuid.uuid4().hex
    with connection.cursor() as c:
        c.execute(sql)
        for name, group in [
            ("p1_api", None),
            ("p1_worker", None),
            ("p1_reader", "ts_audio_reader"),
            ("p1_pub", "ts_audio_publisher"),
            ("p1_operator", "ts_audio_operator"),
        ]:
            c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", [name])
            if not c.fetchone():
                c.execute(
                    "CREATE ROLE "
                    + name
                    + " LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %s",
                    [password],
                )
            else:
                c.execute("ALTER ROLE " + name + " PASSWORD %s", [password])
            if group:
                c.execute("GRANT " + group + " TO " + name)
    params = connection.get_connection_params()
    params.pop("cursor_factory", None)
    opened = []

    def login(name):
        args = {
            k: v
            for k, v in params.items()
            if k in ("dbname", "host", "port", "sslmode")
        }
        args.update(user=name, password=password)
        c = psycopg2.connect(**args)
        c.autocommit = True
        opened.append(c)
        with c.cursor() as cur:
            cur.execute(
                "SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user"
            )
            assert cur.fetchone() == (name, False)
        return c

    yield login
    for c in opened:
        c.close()


def execute(c, sql, args=()):
    with c.cursor() as q:
        q.execute(sql, args)
        if q.description:
            return q.fetchall()


def authority(op, purpose):
    uid = U()
    expiry = int(time.time() * 1000) + 600000
    key = (
        __import__("base64")
        .urlsafe_b64encode(__import__("secrets").token_bytes(32))
        .decode()
        .rstrip("=")
    )
    p = {
        "schema_version": "production-producer-enrollment.v1",
        "enrollment_id": uid,
        "issuer_id": "test-issuer",
        "key_id": uid,
        "public_key": key,
        "environment": "isolated-test",
        "audience": "p1-test",
        "image_hash": H,
        "profile_hash": H,
        "purposes": [purpose],
        "window": {"issued_ms": 0, "expires_ms": expiry},
    }
    execute(
        op,
        "INSERT INTO audio_producer_authority(id,payload,payload_hash,canonical_payload,created_at,issuer_id,key_id,purpose,environment,public_key,image_hash,profile_hash,not_before_ms,expires_ms,signed_enrollment) VALUES(%s,%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,0,%s,%s)",
        (
            uid,
            json.dumps(p),
            content_hash(p["schema_version"], p),
            canonical_json(p),
            "test-issuer",
            uid,
            purpose,
            "isolated-test",
            key,
            H,
            H,
            expiry,
            "isolated test statement",
        ),
    )
    cid = U()
    execute(op, "SELECT audio_p1_create_control(%s,%s,%s)", (cid, "authority", uid))
    transition(op, cid, 0, "activate", {})
    return uid, cid


def transition(c, cid, epoch, action, p):
    return execute(
        c,
        "SELECT audio_p1_transition(%s,%s,%s,%s,%s)",
        (
            cid,
            epoch,
            U(),
            action,
            canonical_json(
                {
                    "schema_version": "production-producer-control.v1",
                    "control_id": cid,
                    "expected_epoch": epoch,
                    "action": action,
                    "window": {
                        "issued_ms": int(time.time() * 1000) - 100,
                        "expires_ms": int(time.time() * 1000) + 120000,
                    },
                    **p,
                }
            ),
        ),
    )[0][0]


def original(pg):
    op = pg("p1_operator")
    pub = pg("p1_pub")
    a, ac = authority(op, "publisher.context.v1")
    tts, tc = authority(op, "tts.original-observation.v1")
    ex = U()
    page = {"page_key": "s1-p2", "source_slide_index": 0, "split_index": 1}
    p = {
        "schema_version": "production-producer-execution.v1",
        "execution_id": ex,
        "scope": {"owner_id": "1", "project_id": "2", "page": page},
        "job_id": "3",
        "speech": {},
    }
    execute(
        pub,
        "INSERT INTO audio_producer_execution(id,payload,payload_hash,canonical_payload,created_at,owner_id,project_id,job_id,page_key,source_slide_index,split_index,planned_speech_hash,issuer_id) VALUES(%s,%s,%s,%s,now(),1,2,3,%s,0,1,%s,%s)",
        (
            ex,
            json.dumps(p),
            content_hash(p["schema_version"], p),
            canonical_json(p),
            "s1-p2",
            content_hash("production-speech-semantics.v1", {}),
            a,
        ),
    )
    cid = U()
    execute(pub, "SELECT audio_p1_create_control(%s,%s,%s)", (cid, "execution", ex))
    transition(pub, cid, 0, "activate", {})
    return op, pub, ex, cid, a, ac, tts, tc


def issue(pub, cid, epoch=1, generation=1):
    n = U()
    ctx = "sha256:" + uuid.uuid4().hex * 2
    transition(
        pub,
        cid,
        epoch,
        "attempt_issued",
        {
            "generation": generation,
            "nonce": n,
            "context_hash": ctx,
            "expires_ms": int(time.time() * 1000) + 120000,
        },
    )
    return n, ctx


def test_pg01_runtime_raw_sql_denied(pg):
    op, pub, ex, cid, a, ac, tts, tc = original(pg)
    for role in ["p1_api", "p1_worker", "p1_reader"]:
        c = pg(role)
        for stmt in [
            "INSERT INTO audio_producer_receipt(id) VALUES(gen_random_uuid())",
            "UPDATE audio_producer_control SET epoch=999",
            "DELETE FROM audio_producer_execution",
            "TRUNCATE audio_producer_receipt CASCADE",
            "ALTER TABLE audio_producer_receipt OWNER TO " + role,
            "ALTER TABLE audio_producer_receipt DISABLE TRIGGER ALL",
            "SET ROLE ts_audio_owner",
            "COPY audio_producer_receipt FROM STDIN",
            "INSERT INTO audio_producer_authority(id) VALUES(gen_random_uuid())",
        ]:
            with pytest.raises(psycopg2.Error):
                execute(c, stmt)
        with pytest.raises(psycopg2.Error):
            transition(c, ac, 1, "revoke", {})


def test_pg02_purpose_and_immutable_guards(pg):
    op, pub, ex, cid, a, ac, tts, tc = original(pg)
    with pytest.raises(psycopg2.Error):
        transition(pub, ac, 1, "revoke", {})
    for c in [op, pub]:
        for stmt in [
            "UPDATE audio_producer_authority SET environment='evil'",
            "DELETE FROM audio_producer_execution",
            "TRUNCATE audio_producer_authority CASCADE",
        ]:
            with pytest.raises(psycopg2.Error):
                execute(c, stmt)
    with pytest.raises(psycopg2.Error):
        execute(pub, "SELECT audio_p1_create_control(%s,%s,%s)", (U(), "authority", a))


def test_pg03_rollback_and_scope(pg):
    op, pub, ex, cid, a, ac, tts, tc = original(pg)
    pub.autocommit = False
    issue(pub, cid)
    pub.rollback()
    pub.autocommit = True
    assert execute(
        pub, "SELECT epoch,generation FROM audio_producer_control WHERE id=%s", (cid,)
    ) == [(1, 0)]
    reader = pg("p1_reader")
    with pytest.raises(psycopg2.Error):
        execute(
            reader,
            "SELECT audio_p1_lock_controls(%s::uuid[],%s::bigint[],%s,%s)",
            ([ac, cid], [1, 1], 9, 2),
        )


def test_pg04_concurrent_generation_cas(pg):
    op, pub, ex, cid, a, ac, tts, tc = original(pg)
    other = pg("p1_pub")

    def attempt(c):
        try:
            issue(c, cid)
            return True
        except psycopg2.Error:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, [pub, other])) == [False, True]
    assert execute(
        pub, "SELECT epoch,generation FROM audio_producer_control WHERE id=%s", (cid,)
    ) == [(2, 1)]


def test_pg05_revocation_serializes_with_reader(pg):
    op, pub, ex, cid, a, ac, tts, tc = original(pg)
    reader = pg("p1_reader")
    reader.autocommit = False
    execute(
        reader,
        "SELECT audio_p1_lock_controls(%s::uuid[],%s::bigint[],1,2)",
        ([ac, cid], [1, 1]),
    )
    with ThreadPoolExecutor(1) as pool:
        f = pool.submit(transition, op, ac, 1, "revoke", {})
        # A bounded SQL lock timeout proves the separate connection is blocked.
        time.sleep(0.1)
        assert not f.done()
        reader.rollback()
        assert f.result(timeout=5) == 2
    reader.autocommit = True
    with pytest.raises(psycopg2.Error):
        execute(
            reader,
            "SELECT audio_p1_lock_controls(%s::uuid[],%s::bigint[],1,2)",
            ([ac, cid], [1, 1]),
        )


def test_pg06_shadow_and_public_execute(pg):
    op, pub, ex, cid, a, ac, tts, tc = original(pg)
    reader = pg("p1_reader")
    execute(
        reader,
        "CREATE TEMP TABLE audio_producer_control(id uuid,epoch bigint,status text)",
    )
    execute(reader, "INSERT INTO audio_producer_control VALUES(%s,999,'active')", (ac,))
    with pytest.raises(psycopg2.Error):
        execute(
            reader,
            "SELECT public.audio_p1_lock_controls(%s::uuid[],%s::bigint[],1,2)",
            ([ac], [999]),
        )
    with pytest.raises(psycopg2.Error):
        execute(
            pg("p1_api"),
            "SELECT public.audio_p1_lock_controls(%s::uuid[],%s::bigint[],1,2)",
            ([ac], [1]),
        )


def test_pg07_nonce_expiry_replay_and_missing(pg):
    op, pub, ex, cid, a, ac, tts, tc = original(pg)
    with pytest.raises(psycopg2.Error):
        transition(
            pub,
            cid,
            1,
            "attempt_issued",
            {"generation": 1, "nonce": U(), "context_hash": H, "expires_ms": 1},
        )
    n, ctx = issue(pub, cid)
    with pytest.raises(psycopg2.Error):
        transition(
            pub,
            cid,
            2,
            "attempt_issued",
            {
                "generation": 2,
                "nonce": n,
                "context_hash": ctx,
                "expires_ms": int(time.time() * 1000) + 120000,
            },
        )
    assert execute(
        pub, "SELECT epoch,generation FROM audio_producer_control WHERE id=%s", (cid,)
    ) == [(2, 1)]
    with pytest.raises(psycopg2.Error):
        execute(
            pg("p1_reader"),
            "SELECT audio_p1_lock_controls(%s::uuid[],%s::bigint[],1,2)",
            ([U()], [1]),
        )


def test_pg08_non_escalating_revoke_and_retry(pg):
    op, pub, ex, cid, a, ac, tts, tc = original(pg)
    other = pg("p1_pub")
    execute(pub, "SET lock_timeout='2s'")
    execute(op, "SET lock_timeout='2s'")
    with ThreadPoolExecutor(2) as pool:
        f = pool.submit(issue, other, cid)
        g = pool.submit(transition, op, ac, 1, "revoke", {})
        try:
            f.result(timeout=5)
        except psycopg2.Error:
            pass
        assert g.result(timeout=5) == 2
    with pytest.raises(psycopg2.Error):
        issue(pub, cid, 2, 2)


def test_pg_receipt_requires_current_observation_and_atomic_acceptance(pg):
    op, pub, ex, cid, a, ac, tts, tc = original(pg)
    issuer, issuer_control = authority(op, "publisher.original-receipt.v1")
    nonce, context_hash = issue(pub, cid)
    event_id = str(
        execute(
            pub,
            "SELECT id FROM audio_producer_event WHERE context_hash=%s",
            (context_hash,),
        )[0][0]
    )
    obsid = U()
    obs = {
        "schema_version": "production-tts-observation.v1",
        "observation_id": obsid,
        "execution_id": ex,
        "generation": 1,
        "nonce": nonce,
        "context_hash": context_hash,
        "outcome": "complete",
        "actual_speech": {},
    }
    obs_hash = content_hash(obs["schema_version"], obs)
    execute(
        pub,
        "INSERT INTO audio_producer_attempt(id,payload,payload_hash,canonical_payload,created_at,execution_id,issuer_id,issued_event_id,generation,nonce,context_hash,outcome,actual_speech_hash,signed_observation) VALUES(%s,%s,%s,%s,now(),%s,%s,%s,1,%s,%s,%s,%s,%s)",
        (
            obsid,
            json.dumps(obs),
            obs_hash,
            canonical_json(obs),
            ex,
            tts,
            event_id,
            nonce,
            context_hash,
            "complete",
            content_hash("production-speech-semantics.v1", {}),
            "isolated SQL test; no signing authority",
        ),
    )
    rid = U()
    body = {
        "schema_version": "production-audio-receipt.v1",
        "receipt_id": rid,
        "observation_hash": obs_hash,
        "context_hash": context_hash,
        "speech_hash": content_hash("production-speech-semantics.v1", {}),
        "proof_hash": H,
        "proof": {
            "owner_id": "1",
            "project_id": "2",
            "page": {"page_key": "s1-p2", "source_slide_index": 0, "split_index": 1},
            "baseline": {"job_id": "3", "run_id": ex},
        },
    }

    def insert():
        execute(
            pub,
            "INSERT INTO audio_producer_receipt(id,payload,payload_hash,canonical_payload,created_at,execution_id,attempt_id,issuer_id,proof_hash,signed_receipt) VALUES(%s,%s,%s,%s,now(),%s,%s,%s,%s,%s)",
            (
                rid,
                json.dumps(body),
                content_hash(body["schema_version"], body),
                canonical_json(body),
                ex,
                obsid,
                issuer,
                H,
                "isolated SQL test; no production signature",
            ),
        )

    # The independent publisher is trusted to validate signatures; DB refuses
    # even that role a receipt without atomic generation/control acceptance.
    with pytest.raises(psycopg2.Error):
        insert()
    pub.autocommit = False
    insert()
    rcid = U()
    execute(pub, "SELECT audio_p1_create_control(%s,%s,%s)", (rcid, "receipt", rid))
    transition(pub, rcid, 0, "activate", {})
    assert transition(pub, cid, 2, "accept", {"receipt_id": rid}) == 3
    pub.commit()
    pub.autocommit = True
    assert (
        str(
            execute(
                pub,
                "SELECT accepted_receipt_id FROM audio_producer_control WHERE id=%s",
                (cid,),
            )[0][0]
        )
        == rid
    )
    with pytest.raises(psycopg2.Error):
        issue(pub, cid, 3, 2)
    with pytest.raises(psycopg2.Error):
        execute(pub, "UPDATE audio_producer_receipt SET proof_hash=%s", (H,))
