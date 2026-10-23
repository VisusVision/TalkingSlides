"""Private pins and genuine fixture media. No jobs, synthesis or segment output.

Native tests use the installed FFmpeg/ffprobe, separately from pure contracts.
All trusted producer associations here are EXPLICIT engineering-only fixtures.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from core.visual_design.schemas import DesignError, canonical_json, content_hash
from core.visual_design.render_snapshot import FrozenRenderSnapshot, revision_hash
from core.visual_segment_artifacts import (PrivateSegmentMedia, MediaPinBudget, ProducerArtifact,
    ProducerAssociation, ApprovedProducerArtifacts, NativeAudioValidator, pin_verified_narration, ROLE_TYPES)

FIXTURES = Path(__file__).resolve().parents[1]/"fixtures/visual_segments"


def load(name): return json.loads((FIXTURES/name).read_text())


def asset(data, extension):
    return {"sha256":"sha256:"+hashlib.sha256(data).hexdigest(),"size":len(data),"extension":extension}


class FixtureTrust:
    """Reviewed committed fixture records, independent of any supplied request."""
    def __init__(self):
        trust = load("trust_anchors.json")
        self.records = {}
        self.authorized = True
        for i in range(3):
            recorded = load(f"audio_proof_{i}.json")
            imports = tuple((role, ProducerArtifact("engineering",object_id,canonical_json(recorded[group][key])))
                for role,object_id,group,key in [("narration","speech","audio","asset"),("source_wav","original-speech","processing","input_asset")])
            self.records[recorded["receipt_id"]] = ProducerAssociation(receipt_id=recorded["receipt_id"],producer_id=trust["producer_id"],
                expected_proof_hash=trust["audio_proof_hashes"][i],owner_id="801",project_id="701",
                page_key=recorded["page"]["page_key"],source_slide_index=recorded["page"]["source_slide_index"],split_index=recorded["page"]["split_index"],
                baseline_job_id="synthetic-baseline-job",baseline_run_id="synthetic-baseline-run",baseline_manifest_hash=recorded["baseline"]["manifest_hash"],
                artifacts=imports,engineering_only=True)
    def lookup(self, receipt_id): return self.records.get(receipt_id)
    def currently_authorized(self, association): return self.authorized


@pytest.fixture
def storage(tmp_path, settings):
    settings.STORAGE_ROOT = str(tmp_path/"public")
    settings.MEDIA_ROOT = str(tmp_path/"media")
    settings.STATIC_ROOT = str(tmp_path/"static")
    settings.VISUAL_RENDER_PRIVATE_ROOT = str(tmp_path/"private")
    settings.VISUAL_SEGMENT_PRODUCER_ROOTS = {"engineering":str(tmp_path/"producer")}
    source = tmp_path/"producer"/"801"/"701"/"synthetic-baseline-run"
    for directory in (tmp_path/"producer",source.parents[1],source.parent,source):
        directory.mkdir(mode=0o700,parents=True,exist_ok=True); directory.chmod(0o700)
    for name, fixture in (("speech.m4a","narration.m4a"),("original-speech.wav","narration.wav")):
        file = source/name; file.write_bytes((FIXTURES/fixture).read_bytes()); file.chmod(0o600)
    return PrivateSegmentMedia("801","701"), FixtureTrust(), source


@pytest.fixture
def native():
    ffmpeg,ffprobe = shutil.which("ffmpeg"),shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        if os.environ.get("VISUAL_MEDIA_REQUIRE") == "1": pytest.fail("Required native audio verification runtime unavailable")
        pytest.skip("FFmpeg/ffprobe unavailable; actual audio validation NOT executed")
    def digest(path): return "sha256:"+hashlib.sha256(Path(path).read_bytes()).hexdigest()
    # Test operator trusts these installed executables, not data from a proof.
    return NativeAudioValidator(ffmpeg=ffmpeg,ffprobe=ffprobe,expected_ffmpeg_hash=digest(ffmpeg),expected_ffprobe_hash=digest(ffprobe))


def eligible(storage,native=None,*,i=0,value=None,**overrides):
    store,trust,_ = storage; t = load("trust_anchors.json")
    return pin_verified_narration(value or load(f"request_{i}.json"),**{
        "snapshot":FrozenRenderSnapshot(load("snapshot.json")),"snapshot_id":t["snapshot_id"],"expected_snapshot_hash":t["snapshot_hash"],
        "owner_id":"801","project_id":"701","store":store,"trusted_source":trust,"verifier":native,
        "supported_runtime_hashes":{t["runtime_hash"]},"allow_engineering":True,**overrides})


def pin_file(storage, data=b"bounded source", role="source_wav"):
    store,_,source = storage
    path = source/"direct.wav"; path.write_bytes(data); path.chmod(0o600)
    with path.open("rb") as file: return store.pin_from_fd(file.fileno(),asset(data,ROLE_TYPES[role][0]),role)


def object_path(store,pin):
    return store.root/"segments-v1"/store.owner_id/store.project_id/"objects"/pin["role"]/(pin["asset"]["sha256"][7:]+"."+pin["asset"]["extension"])


def test_private_pin_read_integrity_permissions_and_no_public_locator(storage,settings):
    store,_,source = storage; pin = pin_file(storage)
    assert store.read(pin) == b"bounded source"
    assert set(pin)=={"schema_version","owner_id","project_id","role","asset"}
    path = object_path(store,pin)
    assert path.stat().st_mode & 0o777 == 0o600
    assert all((p.stat().st_mode&0o777)==0o700 for p in [store.root,path.parent,path.parent.parent])
    assert "url" not in canonical_json(pin) and "path" not in canonical_json(pin)
    assert not Path(settings.STORAGE_ROOT).exists()


@pytest.mark.parametrize("i",range(3))
def test_genuine_attested_audio_pins_with_real_format_packets_and_source_scope(storage,native,i):
    store,_,_ = storage; result = eligible(storage,native,i=i)
    assert result.eligible, result
    assert result.reason_code == "eligible_verified_reuse" and not result.execution_enabled
    assert result.execution_hash == load("trust_anchors.json")["execution_hashes"][i]
    assert all(getattr(result,k) for k in ["proof_validated","producer_authenticated","bytes_verified","media_verified","pin_verified"])
    assert store.read(result.pin) == (FIXTURES/"narration.m4a").read_bytes()
    assert store.read(result.supporting_pins["source_wav"]) == (FIXTURES/"narration.wav").read_bytes()
    report = json.loads(result.verification_json)
    assert report["duration_ms"] == 2335 and report["sample_rate"] == 44100 and report["channels"] == 1
    assert report["decoded_bytes"] > 10000 and report["spoken_content_verified_by_energy"] is False
    assert result.dependencies["page"] == load(f"audio_proof_{i}.json")["page"]
    assert eligible(storage,native,i=i).pin == result.pin
    assert store.budget.bytes_claimed == 15501+74764


def test_independent_lookup_missing_and_synthetic_production_rejection(storage):
    for options in ({"trusted_source":None},{"allow_engineering":False}):
        result = eligible(storage,**options)
        assert not result.eligible and result.reason_code=="audio_provenance_unavailable"
        assert result.pin is None and not result.producer_authenticated
    storage[1].records.clear()
    assert eligible(storage).reason_code=="audio_provenance_unavailable"


def test_self_consistent_forgery_does_not_authenticate_against_independent_source(storage):
    value = load("request_0.json"); value["audio_proof"]["audio"]["packet_payload_hash"]="sha256:"+"0"*64
    value["narration"]["audio_proof_hash"]=content_hash("visual-segment-audio-proof.v1",value["audio_proof"])
    result=eligible(storage,value=value)
    assert not result.eligible and not result.producer_authenticated and result.pin is None


@pytest.mark.parametrize("field,value",[("owner_id","802"),("project_id","702"),("page_key","foreign-page"),
    ("source_slide_index",2),("split_index",0),("source_slide_index",False),("baseline_run_id","foreign-run")])
def test_cross_owner_project_source_split_and_baseline_associations_rejected(storage,field,value):
    trust=storage[1]; key=load("audio_proof_0.json")["receipt_id"]
    trust.records[key]=replace(trust.records[key],**{field:value})
    result=eligible(storage)
    assert not result.eligible and result.reason_code=="invalid_audio_scope" and result.pin is None


def test_revoked_unavailable_or_failing_trust_source_fails_closed(storage):
    storage[1].authorized=False
    result=eligible(storage)
    assert result.reason_code=="audio_ineligible" and result.detail_code=="producer_revoked_or_unavailable"
    class Unavailable:
        def lookup(self,receipt): raise ConnectionError("offline")
    assert eligible(storage,trusted_source=Unavailable()).reason_code=="audio_provenance_unavailable"


def test_unavailable_runtime_is_explicit_not_a_mocked_success(storage):
    result=eligible(storage)
    assert result.reason_code=="unsafe_or_unavailable_runtime" and not result.bytes_verified and result.pin is None


@pytest.mark.parametrize("attack",["same_size","truncated","oversized","symlink","missing"])
def test_wrong_source_bytes_are_never_recertified_or_replaced_by_another_file(storage,native,attack,tmp_path):
    source=storage[2]/"speech.m4a"
    if attack=="same_size":
        data=bytearray(source.read_bytes());data[-1]^=1;source.write_bytes(data)
    if attack=="truncated": source.write_bytes(source.read_bytes()[:-1])
    if attack=="oversized": source.write_bytes(b"x"*(16*1024*1024+1))
    if attack=="symlink":
        external=tmp_path/"foreign.m4a";external.write_bytes(source.read_bytes());source.unlink();source.symlink_to(external)
    if attack=="missing": source.unlink()
    result=eligible(storage,native)
    assert not result.eligible and result.reason_code=="invalid_or_corrupt_artifact" and result.pin is None
    assert not list(storage[0].root.glob("**/*.m4a"))


@pytest.mark.parametrize("field,bad",[("object_id","../speech"),("object_id","/etc/passwd"),("object_id","https://evil.invalid/audio"),
    ("object_id","speech.m4a"),("namespace","../engineering"),("namespace","https://evil.invalid")])
def test_import_handles_never_accept_arbitrary_paths_or_urls(storage,native,field,bad):
    trust=storage[1];key=load("audio_proof_0.json")["receipt_id"];assoc=trust.records[key]
    imports=dict(assoc.artifacts);imports["narration"]=replace(imports["narration"],**{field:bad})
    trust.records[key]=replace(assoc,artifacts=tuple(imports.items()))
    result=eligible(storage,native)
    assert not result.eligible and result.pin is None


def test_media_role_extension_size_and_reference_scope_are_strict(storage):
    store=storage[0];pin=pin_file(storage)
    attacks=[{**pin,"path":"../bad"},{**pin,"owner_id":"802"},{**pin,"project_id":"702"},{**pin,"schema_version":"old"},
        {**pin,"role":"svg"},{**pin,"asset":{**pin["asset"],"extension":"svg"}},
        {**pin,"asset":{**pin["asset"],"size":16*1024*1024+1}},{**pin,"asset":{**pin["asset"],"size":True}}]
    for bad in attacks:
        with pytest.raises(DesignError): store.read(bad)
    for role in ("srt","vtt"):
        with pytest.raises(DesignError): store.reference(asset(b"x"*(128*1024+1),role),role)
    with pytest.raises(DesignError): store.reference({"sha256":"sha256:"+"a"*64,"size":32*1024*1024+1,"extension":"mp4"},"segment")
    assert not list(store.root.glob("**/*.mp4"))


def test_existing_hash_named_file_tampering_symlinks_and_unreadable_objects_fail(storage,tmp_path):
    store=storage[0];pin=pin_file(storage);path=object_path(store,pin)
    path.write_bytes(b"x"*pin["asset"]["size"])
    with pytest.raises(DesignError): store.read(pin)
    with pytest.raises(DesignError): pin_file(storage)
    assert path.read_bytes()==b"x"*pin["asset"]["size"]
    path.unlink();foreign=tmp_path/"foreign";foreign.write_bytes(b"bounded source");path.symlink_to(foreign)
    with pytest.raises(OSError): store.read(pin)
    path.unlink();path.write_bytes(b"bounded source");path.chmod(0)
    with pytest.raises((DesignError, OSError)): store.read(pin)
    path.unlink()
    with pytest.raises(FileNotFoundError): store.read(pin)


def test_concurrent_identical_pins_are_atomic_and_deduplicated(storage):
    store,_,source=storage;data=(FIXTURES/"narration.m4a").read_bytes();expected=asset(data,"m4a")
    def save(_):
        with (source/"speech.m4a").open("rb") as file:
            return store.pin_from_fd(file.fileno(),expected,"narration")
    with ThreadPoolExecutor(max_workers=8) as pool: pins=list(pool.map(save,range(16)))
    assert all(p==pins[0] for p in pins) and store.read(pins[0])==data
    assert len(list(store.root.glob("**/*.m4a")))==1 and not list(store.root.glob("**/pin-*"))
    assert store.budget.bytes_claimed==len(data)


def test_failed_validation_leaves_no_success_pin_or_budget_claim(storage):
    store,_,source=storage;data=(source/"speech.m4a").read_bytes();expected=asset(data,"m4a")
    def fail(fd): raise DesignError("audio_format","Unverified media")
    with (source/"speech.m4a").open("rb") as file:
        with pytest.raises(DesignError): store.pin_from_fd(file.fileno(),expected,"narration",validate_staged=fail)
    assert not list(store.root.glob("**/*.m4a")) and not list(store.root.glob("**/pin-*")) and store.budget.bytes_claimed==0


def test_in_place_source_mutation_during_copy_fails_without_misleading_pin(storage,monkeypatch):
    import core.visual_segment_artifacts as module
    store,_,source=storage;path=source/"speech.m4a";data=path.read_bytes();expected=asset(data,"m4a")
    original=module.os.write;changed=[]
    def write(fd,block):
        written=original(fd,block)
        if not changed:
            changed.append(True);path.write_bytes(b"x"*len(data))
        return written
    monkeypatch.setattr(module.os,"write",write)
    with path.open("rb") as file:
        with pytest.raises(DesignError): store.pin_from_fd(file.fileno(),expected,"narration")
    assert not list(store.root.glob("**/*.m4a"))


def test_private_bytes_remain_original_when_import_path_is_replaced_after_pin(storage,native):
    result=eligible(storage,native);assert result.eligible,result
    (storage[2]/"speech.m4a").write_bytes(b"x"*15501)
    assert storage[0].read(result.pin)==(FIXTURES/"narration.m4a").read_bytes()
    assert not eligible(storage,native).eligible


def test_root_configuration_and_directory_symlinks_fail(storage,settings,tmp_path):
    for raw in (None,"relative",str(tmp_path/"public"/"private"),str(tmp_path/"media"/"private"),str(tmp_path/"static"/"private")):
        settings.VISUAL_RENDER_PRIVATE_ROOT=raw
        with pytest.raises(DesignError): PrivateSegmentMedia("801","701")
    settings.VISUAL_RENDER_PRIVATE_ROOT=str(tmp_path/"private-link")
    outside=tmp_path/"outside";outside.mkdir(mode=0o700);Path(settings.VISUAL_RENDER_PRIVATE_ROOT).symlink_to(outside,target_is_directory=True)
    store=PrivateSegmentMedia("801","701")
    with (storage[2]/"speech.m4a").open("rb") as file:
        with pytest.raises(OSError): store.pin_from_fd(file.fileno(),load("audio_proof_0.json")["audio"]["asset"],"narration")


def test_budget_is_separate_from_legacy_image_font_limits():
    from core.visual_render_assets import MAX_ASSET_BYTES, MAX_TOTAL_BYTES
    assert MAX_ASSET_BYTES==8*1024*1024 and MAX_TOTAL_BYTES==32*1024*1024
    budget=MediaPinBudget()
    with budget.claim("audio",16*1024*1024): pass
    with budget.claim("video",32*1024*1024): pass
    with budget.claim("source",16*1024*1024): pass
    with budget.claim("audio",16*1024*1024): pass
    with pytest.raises(DesignError):
        with budget.claim("caption",1): pass
    assert budget.bytes_claimed==64*1024*1024


def test_visual_only_revision_preserves_real_audio_eligibility_and_exact_private_bytes(storage,native):
    from core.visual_render_planning import build_snapshot_planning_manifests,compare_snapshot_plans
    from core.visual_design.segment_contracts import describe_scene
    from core.visual_design.segment_dependencies import VISUAL_VERSION
    first=eligible(storage,native);assert first.eligible,first
    old=FrozenRenderSnapshot(load("snapshot.json"));raw=old.to_dict();raw["fonts"]["regular"]=deepcopy(raw["fonts"]["bold"])
    raw["revision_hash"]=revision_hash(raw);new=FrozenRenderSnapshot(raw)
    req=load("request_0.json");req["snapshot"].update(snapshot_hash=new.content_hash,authored_revision_hash=raw["revision_hash"])
    req["scene"]=describe_scene(new,req["page"]["page_key"]);req["recipe"]["fonts_hash"]=content_hash("visual-segment-fonts.v1",req["scene"]["fonts"])
    second=eligible(storage,native,value=req,snapshot=new,expected_snapshot_hash=new.content_hash)
    assert second.eligible,second
    assert second.pin==first.pin and second.dependencies==first.dependencies
    assert second.execution_hash != first.execution_hash
    env={"schema_version":VISUAL_VERSION,"runtime":req["recipe"]["runtime"],"font_configuration":{"schema_version":"pinned-font-configuration.v1", "family":"DejaVu Sans","configuration_hash":"sha256:"+"a"*64}}
    dep={req["page"]["page_key"]:first.dependencies}
    before=build_snapshot_planning_manifests(old,visual_environment=env,synthesis_dependencies_by_page_key=dep)
    after=build_snapshot_planning_manifests(new,visual_environment=env,synthesis_dependencies_by_page_key=dep)
    report=compare_snapshot_plans(before,after,required_artifacts=())
    assert report["plan"]["summary"]["recompose_visual_only_future"]==3 and report["execution_enabled"] is False


def test_revocation_during_validation_prevents_eligible_result_without_mutating_pins(storage,native,monkeypatch):
    original=native.validate
    def validate(fd,proof):
        result=original(fd,proof);storage[1].authorized=False;return result
    monkeypatch.setattr(native,"validate",validate)
    result=eligible(storage,native)
    assert not result.eligible and result.reason_code=="audio_ineligible" and result.pin is None
    assert result.media_verified and result.bytes_verified  # verified history is not live permission


def test_missing_private_pin_after_copy_never_produces_reuse_eligibility(storage,native,monkeypatch):
    original=storage[0].pin_from_fd
    def pin(*args,**kwargs):
        result=original(*args,**kwargs)
        if result["role"]=="narration":object_path(storage[0],result).unlink()
        return result
    monkeypatch.setattr(storage[0],"pin_from_fd",pin)
    result=eligible(storage,native)
    assert not result.eligible and result.reason_code=="missing_private_pin"


@pytest.mark.parametrize("field,bad",[("sample_rate",48000),("channels",2),("duration_ms",2000),("profile","HE-AAC"),("packet_payload_hash","sha256:"+"a"*64)])
def test_native_verifier_checks_real_audio_properties_and_packet_payload(storage,native,field,bad):
    proof=load("audio_proof_0.json");proof["audio"][field]=bad
    with (storage[2]/"speech.m4a").open("rb") as file:
        with pytest.raises(DesignError):native.validate(file.fileno(),proof)


def test_native_runtime_identity_mismatch_and_native_timeout_are_fail_closed(storage,native,monkeypatch):
    with pytest.raises(DesignError):
        NativeAudioValidator(ffmpeg=native.ffmpeg,ffprobe=native.ffprobe,expected_ffmpeg_hash="sha256:"+"a"*64,expected_ffprobe_hash=native.runtime["ffprobe_hash"])
    import core.visual_segment_artifacts as module
    def timeout(*args,**kwargs):raise DesignError("audio_runtime","Bounded native timeout")
    monkeypatch.setattr(native,"_run",timeout)
    result=eligible(storage,native)
    assert not result.eligible and result.reason_code=="unsafe_or_unavailable_runtime" and result.pin is None


def test_changed_narration_and_snapshot_voice_are_incompatible_not_synthesized(storage,native):
    raw=load("snapshot.json");raw["state"]["pages"][0]["narration_text"]="Later narration."
    raw["revision_hash"]=revision_hash(raw);frozen=FrozenRenderSnapshot(raw)
    result=eligible(storage,native,snapshot=frozen,expected_snapshot_hash=frozen.content_hash)
    assert result.reason_code=="incompatible_narration" and not result.eligible
    raw=load("snapshot.json");raw["state"]["voice"]["voice_id"]="different-voice";raw["revision_hash"]=revision_hash(raw)
    frozen=FrozenRenderSnapshot(raw);value=load("request_0.json")
    value["snapshot"].update(snapshot_hash=frozen.content_hash,authored_revision_hash=raw["revision_hash"])
    result=eligible(storage,native,value=value,snapshot=frozen,expected_snapshot_hash=frozen.content_hash)
    assert result.reason_code=="incompatible_synthesis_dependencies" and not result.eligible


def test_no_django_records_jobs_or_publication_mutation(storage,native):
    # No DB is allowed for this test. Accidental ORM access would raise pytest's
    # database-access guard; production dispatch/finalization are import-blocked.
    import sys
    with patch.dict(sys.modules,{"core.drafts":None,"worker.tasks":None,"celery":None}):
        result=eligible(storage,native)
    assert result.eligible,result
    assert result.execution_enabled is False
    assert not list(storage[0].root.glob("**/*.mp4"))


def test_source_namespace_and_private_project_directory_symlinks_cannot_escape(storage,native,tmp_path):
    store,_,source=storage
    moved=source.with_name("saved-run");source.rename(moved);source.symlink_to(moved,target_is_directory=True)
    assert not eligible(storage,native).eligible
    source.unlink();moved.rename(source)
    pin=pin_file(storage);project=object_path(store,pin).parents[2]
    saved=project.with_name("saved-project");project.rename(saved);project.symlink_to(saved,target_is_directory=True)
    with pytest.raises(OSError):store.read(pin)


def test_source_replacement_between_hash_and_copy_never_accepts_replacement(storage,monkeypatch):
    import core.visual_segment_artifacts as module
    store,_,source=storage;path=source/"speech.m4a";expected=asset(path.read_bytes(),"m4a")
    original=module._digest_fd;calls=[]
    def verify(fd,ref):
        result=original(fd,ref)
        if not calls:
            calls.append(True);path.write_bytes(b"x"*ref["size"])
        return result
    monkeypatch.setattr(module,"_digest_fd",verify)
    with path.open("rb") as file:
        with pytest.raises(DesignError):store.pin_from_fd(file.fileno(),expected,"narration")
    assert not list(store.root.glob("**/*.m4a"))


def test_expected_hash_and_required_source_associations_come_from_trust_lookup(storage):
    trust=storage[1];key=load("audio_proof_0.json")["receipt_id"];association=trust.records[key]
    imports=dict(association.artifacts)
    wrong=imports["narration"].asset();wrong["sha256"]="sha256:"+"f"*64
    imports["narration"]=replace(imports["narration"],asset_json=canonical_json(wrong))
    trust.records[key]=replace(association,artifacts=tuple(imports.items()))
    assert not eligible(storage).eligible
    trust.records[key]=replace(association,artifacts=tuple((r,a) for r,a in association.artifacts if r!="source_wav"))
    assert not eligible(storage).eligible


def test_actual_native_process_timeout_and_output_budget_are_bounded(storage,native,monkeypatch):
    import sys
    import core.visual_segment_artifacts as module
    # Exercise the real bounded subprocess reader, not just a mocked timeout.
    limits={**module.LIMITS,"probe_timeout_seconds":0.1}
    monkeypatch.setattr(module,"LIMITS",limits)
    with (storage[2]/"speech.m4a").open("rb") as file:
        with pytest.raises(DesignError,match="timed out"):
            native._run([sys.executable,"-c","import time; time.sleep(1)"],file.fileno(),output_limit=64)
        with pytest.raises(DesignError,match="budget"):
            native._run([sys.executable,"-c","print('x'*10000)"],file.fileno(),output_limit=64)


def test_storage_budget_rejects_invalid_claims_and_bounded_object_count():
    for size in (True,0,-1,32*1024*1024+1):
        with pytest.raises(DesignError):
            with MediaPinBudget().claim("bad",size):pass
    budget=MediaPinBudget()
    for i in range(128):
        with budget.claim(i,1):pass
    with pytest.raises(DesignError):
        with budget.claim(129,1):pass


def test_native_unavailable_binary_and_malformed_or_empty_audio_are_not_eligible(storage,native,tmp_path):
    with pytest.raises(DesignError,match="unavailable"):
        NativeAudioValidator(ffmpeg=str(tmp_path/"missing"),ffprobe=native.ffprobe,
            expected_ffmpeg_hash=native.runtime["ffmpeg_hash"],expected_ffprobe_hash=native.runtime["ffprobe_hash"])
    # Independently supplied expected bytes alone are not sufficient format proof.
    # This directly tests the native validator, not a trusted producer association.
    for data in (b"not an M4A container",b"\x00"*512):
        proof=load("audio_proof_0.json");proof["audio"]["asset"]=asset(data,"m4a")
        path=tmp_path/"malformed.m4a";path.write_bytes(data)
        with path.open("rb") as file:
            with pytest.raises(DesignError):native.validate(file.fileno(),proof)
