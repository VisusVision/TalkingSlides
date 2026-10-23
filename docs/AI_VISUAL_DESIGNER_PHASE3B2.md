# Phase 3B-2 — Private pins and verified narration eligibility

Baseline: `400e9ca6d7d3519272414fff38ca26e590a25546`, verified on
`feature/ai-visual-designer` with a clean working tree. Scope: artifact storage,
producer/byte/media eligibility, opt-in planner dependencies and regression tests.
There are no new models, migrations, APIs, Runs, Jobs, dispatch, TTS, segment
composition, subtitle files, Studio controls or publication changes.

## Components and boundaries

| Component | Responsibility |
|---|---|
| `core/visual_segment_artifacts.py::PrivateSegmentMedia` | Separate typed, owner/project-scoped, bounded content-addressed media pins |
| `ProducerTrustSource`, `ProducerAssociation`, `ProducerArtifact` | Privileged independent producer lookup, expected digests/scope, opaque approved import handles |
| `ApprovedProducerArtifacts` | No-follow imports from explicitly configured private owner/project/baseline-run namespaces |
| `NativeAudioValidator` | Bounded, read-only FFprobe/FFmpeg audio inspection using operator-pinned executable identities |
| `pin_verified_narration` / `AudioReuseEligibility` | Trust → saved-source binding → verified copy/media → private-pin verification → live control recheck |
| `core/visual_design/segment_dependencies.py` | Pure, strict versioned synthesis, caption and visual-environment fingerprint contracts shared by existing planners |
| Existing core/partial planners and snapshot adapter | Opt-in fingerprints and existing classifications; no second planner or execution path |

All Phase 3A snapshot/frame and Phase 3B-1 request/audio-proof/segment-receipt
contracts, their golden hashes and existing fixtures remain unchanged. Package
exports, legacy worker artifact lookup/recompose functions and dependency files
are untouched.

## Private storage

The store reuses Phase 3A's confinement, expected-digest and atomic-link principles
without widening `PrivateRenderAssets`' v1 8 MiB image/font / 32 MiB capture limits.
New pins use `visual-segment-media-pin.v1` and contain only owner, project, role
and `{sha256,size,extension}`. A reference alone is not proof an object exists;
`pin_from_fd`, `verify`, `read` and `open_verified` enforce actual byte integrity.

| Role | Extension | Limit |
|---|---|---|
| Narration | M4A | 16 MiB |
| Attested source / cloned-voice sample | WAV | 16 MiB each |
| Future segment artifact | MP4 | 32 MiB |
| Future subtitle sidecar | SRT / VTT | 128 KiB each |

Future output types have storage/reference support only, not media acceptance or
generation. No MP4/SRT/VTT is created by this implementation's new tests or code.
One explicit `MediaPinBudget` bounds an attempt to 64 MiB / 128 unique objects;
concurrent identical pins count once. Failed claims roll back. A later persisted
Run must reconstruct/share its aggregate budget instead of creating fresh budgets
to evade the limit. No global quota or retention service is claimed.

`VISUAL_RENDER_PRIVATE_ROOT` must explicitly name persistent local storage,
separate from every configured `STORAGE_ROOT`, `STATIC_ROOT` and `MEDIA_ROOT`.
Layout: `segments-v1/<owner>/<project>/objects/<role>/<digest>.<extension>` under
that root. No URL or public media locator is returned. Existing private image/font
namespaces keep their original layout.

This implementation requires Linux/POSIX directory descriptors, `O_NOFOLLOW` and
`/proc/self/fd`. Root and managed directories require the runtime UID and no group/other
permissions (new directories are 0700); files require that UID, private permissions
and owner readability (new files are 0600). Unsafe existing directories are rejected rather than silently chmodded.
All path components are server-derived opaque/numeric identities. Traversal, URL,
absolute-path, symlink, foreign-scope, special-file, unreadable and corrupt-object
inputs fail. Configured import roots receive the same private-root checks.

Copies and digests stream in 64 KiB chunks. Source size/hash/stat identity are
checked before and after copying. A staged private file is flushed and verified,
then format-validated where required, before atomic no-overwrite linking. Existing
CAS objects must already match expected bytes; they are never overwritten or
silently repaired. The final object is reopened and reverified before success.
Native validation uses the staged descriptor, preventing mutable source paths
from becoming execution inputs. A path replacement can never authorize replacement
bytes: the original expected digest remains authoritative.

Successful supporting pins can remain unreferenced if a later narration/control
check fails. They convey no eligibility; safe reference-based orphan cleanup is a
later stage. Failed individual pin operations remove their staged files and do not
return success references. Filesystem-administrator/same-UID hostile code can still
alter files; every boundary read verifies bytes rather than assuming paths immutable.

## Independent producer trust and eligibility

**No production producer source is implemented or configured.** An absent lookup
returns `audio_provenance_unavailable`; incomplete historical lesson audio is not
retroactively certified. A colocated JSON sidecar, newly calculated checksum or
planner classification is never a trust source.

`ProducerTrustSource.lookup(receipt_id)` is a privileged server dependency. Future
implementations must independently authenticate producer records and supply the
preexisting expected proof digest, producer ID, owner/project/page/source/split,
baseline Job/run/manifest and expected artifact digests/import handles. No browser
may supply that object or its configuration. This interface does not magically
authenticate arbitrary server code; no ledger/signature-service implementation is
claimed. Unavailable/revoked control signals fail closed before and after pinning.
These checks are not transactional revocation/lease enforcement.

The engineering tests load reviewed Phase 3B-1 trust anchors independently of the
request. Both an engineering-only association and explicit `allow_engineering=True`
are required. Production mode rejects the synthetic attestation. No new speech
fixture or synthesis call was added.

`pin_verified_narration` constructs the unchanged `SegmentAudioProof` against the
independent anchor and the unchanged `SegmentRequest` against a validated saved
snapshot/server expected hash. It checks exact scope, protected narration,
spoken-text association, concrete voice/provider/language, neutral supported
controls, synthesis dependencies, captions and recipe. Expected audio/WAV/sample
references must independently match the trusted association. Supporting source WAV
and required cloned-voice sample pins are verified too. There is no fallback to a
latest project path, another provider, fresh TTS or silence.

The immutable `visual-segment-audio-eligibility.v1` result separates producer/proof,
byte, native-media and final private-pin checks. Success binds the exact execution
hash, pin refs, validation report and typed synthesis descriptor; all results keep
`execution_enabled=false`. A visual-only revision receives its own execution hash
while legitimately retaining the same verified audio pin/dependencies.

Machine-readable results: `eligible_verified_reuse`, `audio_provenance_unavailable`,
`audio_ineligible`, `invalid_audio_scope`, `incompatible_narration`,
`incompatible_synthesis_dependencies`, `unsupported_audio_format`,
`invalid_or_corrupt_artifact`, `missing_private_pin`, `structural_page_mismatch`,
`unsafe_or_unavailable_runtime`. A failed result never permits synthesis.

## Actual native audio checks

The operator must provide installed FFmpeg/ffprobe paths and independently expected
binary hashes. Missing, replaced or unsupported tools fail; requests cannot choose
executables, commands, URLs or filters. Per-command deadlines, bounded stdout/
stderr and bounded streaming packet/PCM output are enforced. Native operations
only probe, copy encoded packets into a streaming hash and decode samples for
verification. Pinned input bytes are never re-encoded, trimmed or modified.

The existing 15,501-byte fixture validates as a single AAC-LC M4A audio stream,
44,100 Hz mono, measured 2,335 ms, zero local origin, matching container/decoded
duration within the 80 ms contract tolerance. Container size/SHA-256 and encoded
AAC packet-payload hash match the preexisting attestation. Decode must be nonempty
and contain nonzero signal. The WAV is structurally complete and hash-identical.
**Signal/energy is not evidence of words or voice identity.** Protected/spoken text
association and the producer's measured activity window come from the trusted
producer receipt, not an acoustic heuristic. Real native tests are separate from
the standalone pure contract suite and explicitly skip/fail when tools are absent.

## Planner compatibility

New semantics are explicit opt-ins: `visual-segment-synthesis-dependencies.v1`,
`visual-segment-caption-dependencies.v1`, `visual-segment-visual-environment.v1`.
Synthesis inputs include exact protected/spoken hashes, concrete language/provider/
voice, supported settings, sample/model/config/preparation/pronunciation hashes,
producer/baseline/proof identity, audio/processing/packet identity and verified-media
report identity. Typed fields enter the existing TTS hashes directly; they are not
hidden in canonicalized-away metadata or encoded into fake voice IDs.

The snapshot adapter's new visual mode includes scene/text ownership, candidate,
source/grounded source, SVG/raster, plan/preview, fonts, font configuration and native
runtime/rasterizer identities. New synthesis/caption arguments require an explicit
visual environment. Font/rasterizer-only changes affect visuals and preserve audio
fingerprints/eligibility. Native font/rasterizer identities here are explicit planning
inputs, not a claim that a scene has been rendered or that deployed binaries were
validated for visual execution.

Changes use existing `ProviderChanged` / `tts_settings_changed` TTS invalidation;
missing/unknown private semantics fail as `MissingBaseline` / `unknown_requires_full`.
Cue/hold changes use existing timing/structural classifications without changing
otherwise eligible audio. Reorder/split mapping remains page-key/source/split-based;
structural changes cannot yield positional reuse. Report-only paths retain
`execution_enabled=false`, and classification alone never authorizes audio reuse.

When optional fields are absent, old manifest shapes, canonical bytes, hashes and
classifications remain identical, including the default Phase 3A snapshot adapter.
No global pipeline or manifest version was bumped. The reviewed pre-edit golden
fixture records exact canonical core, expected/finalized partial and snapshot plans
from baseline `400e9ca...`; core hash `db1adad0...`, partial hash `55f067bc...`.

## Executed verification

Local focused command: Phase 3B-1 contracts; new artifact tests; core/partial/snapshot
planner suites; Phase 3A snapshot/frame tests; Phase 1 and grounded design suites:
**600 passed, 1 skipped, 63 subtests passed** (16.12 s). The skip requires PostgreSQL.
Actual FFmpeg/ffprobe packet/codec/duration/decode checks ran successfully; no narrated
video segment was generated. The standalone no-Django/no-native Phase 3B-1 command
also passed **216 tests**. Django check: no issues; migration dry run: no changes.

`npm ci`, **350 frontend tests**, and `npm run build` passed. Both
`npm audit --audit-level=high` and `npm audit --omit=dev --audit-level=moderate`
reported **zero vulnerabilities**. `pip-audit -r services/api/requirements.txt`:
**No known vulnerabilities found**. No dependency files changed.

Final full local backend run: **2,643 passed, 1 failed, 12 skipped, 63 subtests
passed** (106.90 s). The sole failure is the unchanged
`test_musetalk_warmup_cleanup.py::test_orphan_check_detects_and_kills_marked_warmup_process`,
which cannot exercise process-group cleanup correctly in this sandbox. It is not
suppressed or claimed as passing. Hosted validation is required independently.

Hosted full backend, PostgreSQL, Chromium, frontend, audits and Docker gates are
reported for the final commit in the completion report and [feature-branch CI](https://github.com/VisusVision/TalkingSlides/actions?query=branch%3Afeature%2Fai-visual-designer).
Local PostgreSQL/Docker are unavailable; no local container acceptance is claimed.
The known unrelated local MuseTalk process-group limitation is not patched or
silenced. No producer-service attestation, segment media, publication, cross-container
private mount or new Run concurrency acceptance is claimed.

## Remaining prerequisites for 3B-3

1. A real independently authenticated producer receipt source, or an explicitly
   engineering-only increment; legacy files remain ineligible. Verify actual
   producer settings/sample/model/preparation attestations before real-lesson use.
2. Separately authorized immutable Run/Job persistence and exact saved-revision
   reservation, owner checks, deduplication and PostgreSQL transaction/race tests.
3. Revocation/control records and consistent reservation/claim/completion/access
   gates before inferred designs execute. This stage's supplied control predicate
   is not atomic revocation, cancellation or lease fencing.
4. Provision persistent private roots, same runtime UID/mount visibility, native
   verifier availability in the intended execution environment, quotas and safe
   orphan/reference retention. No Compose topology or delivery API was added.
5. Freeze/check real font configuration, fonts and supported rasterizer/media build
   identities at the later rendering boundary. Only then reuse the established
   compositor under its separately authorized stage, with AAC-copy acceptance.

Stop at 3B-2. Persisted Runs, Celery, private video generation, normal Save & Rerender,
legacy worker index-zero fixes and authoritative publication remain unimplemented.
