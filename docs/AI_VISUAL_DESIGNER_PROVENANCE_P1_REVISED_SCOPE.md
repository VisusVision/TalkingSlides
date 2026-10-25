# Production provenance P1 — revised implementation specification

Design revision: 25 October 2026, Europe/Istanbul. Repository
`VisusVision/TalkingSlides`, branch `feature/ai-visual-designer`, source HEAD
`ea04701d9495da290c173a3bc3101a9a5dd6a1ec`.

**P1_REVISION_READY_FOR_IMPLEMENTATION** means that the bounded P1 task specified
here is sufficiently defined for separate implementation authorization. This
revision implements nothing and grants no production availability. No commit,
push, migration, synthesis or deployment is performed.

This document controls the **revised P1 boundary** in the
[production provenance design](AI_VISUAL_DESIGNER_PRODUCTION_PROVENANCE_DESIGN.md).
The preserved [partial-rerender audit](AI_VISUAL_DESIGNER_PARTIAL_RERENDER_INTEGRATION_REVIEW.md)
is authoritative source evidence; its `REVISE_P1_BEFORE_IMPLEMENTATION` conclusion
describes the specification before this revision. Its T01–T22 and the production
design's A01–A32 are proposed acceptance cases, not executed tests. The
[Phase 3B design](AI_VISUAL_DESIGNER_PHASE3B_DESIGN.md) and
[implemented status](AI_VISUAL_DESIGNER_PHASE3B.md) retain their private,
nonpublishing security boundary.

## 1. Verified baseline and partial-rerender assumptions

At revision start local HEAD, remote-tracking ref and a read-only live GitHub ref
request all matched `ea04701`. The index and tracked files were clean. The only
untracked file was the integration review; it is preserved byte for byte. No
unpushed commits or divergent partial-render source were found in this checkout.
No other computer's local/deployed state is inferred. Runtime source is unchanged
between the original design source `601979d` and `ea04701`.

| Verified source | Consequence for P1 |
|---|---|
| `services/worker/tasks.py::export_project` 8254 and `_workspace` 350; per-page `audio_out`/`part_out` descriptors | Existing output-page MP3 and MP4 storage already exists. Do not create a second narration store, narrator or renderer |
| `recompose_visual_only_slide_segment` 8531 | Reuses an MP3 and repaints/re-encodes the target part, but reports provider `cached` and can fall back to synthesis. Preserve the original provider in future producer evidence; do not call this task wholesale from private execution |
| `recompose_narration_only_slide_segment` 8662 | Calls existing TTS for the affected page and can reuse its raster. Future observation must share one protocol with the normal page task, not a parallel TTS API |
| `_build_dirty_dispatch_plan` 1824; `process_pptx_to_video` 10287 | Selected targets, auto dirty pages and forced full draft rendering have distinct behavior. P1 changes none of them |
| `record_dirty_render_dispatch_result` 10192 | Done status can retain old baseline URLs. It does not establish a newly qualified full render or synthesis association |
| `partial_render_manifest.py::build_partial_render_manifest` 355 / `classify_partial_render_changes` 834; `core/render_planner.py::build_render_planner_manifest` 137 | Useful existing component fingerprints, not independent production evidence. Job/order/subtitle changes must not become universal speech keys |
| `services/scripts/tts_client.py::synthesize_with_service_with_metadata` 138 and `tts_preprocess/effective_input.py::prepare_effective_tts_input` 120 | Protected text, normalized/spoken text, prepared chunks and service-consumed chunks are different observations |
| `services/tts_service/main.py::_synthesize_xtts_v2` 737 | Engine WAV pieces/gaps are temporary, joined into MP3 and deleted. No durable chunk-audio reuse is present |
| `core/models.py::Job` 498, `TranscriptPage` 874, `LessonSegment` 1242 | Reuse their existing identifiers. Job association, page values and order/path-based segment rows remain mutable application metadata |
| `tasks.py` 3559/3577/3739/3747 | Truthiness can replace source index zero with output position. New contracts strictly preserve zero; fixing legacy builders is deferred |
| `core/visual_segment_runs.py::_producer_dependencies` 74; `visual_segment_dispatch.py::_deployment_ready` 26 | Production trust is absent and readiness false. Both literal closed defaults remain unchanged in P1 |

## 2. Four identities, with separate hashes

An equality comparison, an execution event, an artifact digest and an access
decision answer different questions. No semantic hash is an authority or a
request to run TTS. Use new versioned domains without altering any existing
snapshot, proof, request, receipt or planner hash.

### 2.1 Original speech synthesis identity

Define a pure `SpeechSemantics` structure containing exact protected/source text
and hash; exact effective spoken text and hash; concrete effective language;
ordered **actual engine input** strings; inter-chunk pause values and gap policy;
segmentation/preparation code/config/resource identities; actual provider/voice;
effective supported synthesis controls/defaults; loaded model,
configuration/tokenizer/library identities; and enrolled immutable sample identity
when applicable. ProducerContext retains selected provider/voice and requested
settings in a separate request-identity projection; the observation binds that
request and the actual result. Do not put ignored request metadata into the
effective speech fingerprint. Changed requested policy still requires explicit
compatibility/current-authorization checks; hash equality never silently approves
a newly requested voice/provider or unsupported control.

`speech_semantics_hash = H(new speech domain, SpeechSemantics)` excludes Job ID,
execution UUID/nonce, output bytes, scene, display text not spoken by the engine,
fonts, lesson order, saved subtitle grouping/timing, absolute start and video hold.
It commits preparation/pronunciation inputs actually consumed, not a current file
hash attributed to a past process. Raw audio and WAV→AAC processing lineage are
bound by the original observation/receipt described below; they are not inputs
to a pre-inference cache key. Equal semantics can produce different waveform
bytes in repeated nondeterministic inference; do not deduplicate actual events.

For private association define an additional scoped comparison:
`scoped_speech_hash = H(new scoped domain, owner_id, project_id, stable_page,
speech_semantics_hash)`. `stable_page` is exactly page key, zero-based source
index and split index. Job IDs stay in signed execution/use bindings, not this
semantic comparison. Identical unscoped hashes never authorize cross-page,
cross-owner or cross-project reuse. Initial reuse permits the **same stable page
tuple within the same owner/project** only; cross-page grants are deferred.

There are two stages: a server-captured **planned** speech identity before
inference, and a producer-observed **actual** identity afterward. Do not populate
actual fields in advance or mutate the original plan to match unexpected output.
Supported initial eligibility requires equality under the approved preparation
profile, exact complete coverage and no fallback/recovery substitution. Both
hashes survive for diagnosis. An `AudioProducerExecution` row is the immutable
context for a logical page synthesis invocation; the actual generation event is
identified by its execution UUID, attempt generation/nonce and signed observation.
It does not assert that a model executed merely because the row exists.
The planned eligible profile resolves concrete expected provider/model/resources;
`auto` may remain a requested policy but is not an actual engine identity. An
ordinary result outside that profile can finish normally without eligibility.

Context can legitimately affect speech. Today's tasks invent `Slide N.` for empty
narration, and a future explicit template could speak a slide number or heading.
If order/context is actually expanded into spoken text, language selection,
pronunciation or engine arguments, that resolved value changes SpeechSemantics.
Capture the exact resolved dependency and template/preparation version. Order
alone does not. P1 must test this distinction without implementing templates or
certifying today's invented fallback as protected narration.

### 2.2 Original producer observation

`OriginalObservation` binds the issued context hash, execution/attempt identity,
audience/environment, exact owner/project/origin Job/stable-page capture, planned
and actual speech identities, actual ordered engine-call coverage, effective
resources, actual provider, output identities and outcome. Successful initial
observations bind genuine original MP3 and retained joined WAV hashes/sizes;
failed/ineligible observations explicitly report missing outputs and cannot
validate as successful original observations. A failed chunk/recovery or unknown
loaded resource cannot be hidden by a nonempty MP3.

TTS signs this at the actual execution boundary in **P2**, not P1. Observation
bytes/signature and original actual provider remain immutable. A later use cannot
reissue it, replace its provider with `cached`, assign a new origin Job or fill
missing model/sample/coverage from today's files. P1 tests this with explicit test
registries, never a production source or fixture-enabled runtime setting.

### 2.3 Audio reuse association and lineage

P1 defines pure `AudioReuseAssociation` validation, **not a persistence table or
accepted reuse endpoint**. Its required fields are:

| Field group | Exact meaning |
|---|---|
| Version, purpose, use UUID, environment/audience, issued/expiry times | Immutable identity of this consuming use; it is not a synthesis execution UUID |
| Parent | Accepted original producer receipt UUID/digest, its original observation UUID/digest and final eligible audio digest/size/type; direct original parent only, no arbitrary chain |
| Origin | Original execution/attempt/context, original Job/manifest and stable page, retained by reference and checked against the authoritative parent |
| Consumer | Existing consuming Job ID/purpose and sealed input-manifest hash; this use UUID, exact current source capture and stable page tuple, presentation hash/order. The Job+sealed manifest identify consuming execution; no new AudioProducerExecution row is created for it |
| Speech comparison | Planned consumer speech/scoped hashes and independently verified parent's actual speech/scoped hashes; all must match supported semantics |
| Authorization | Matching current owner/project, required resource-profile identity, sample/consent version, issuer/receipt/control epochs and bounded purpose; issuance-time observation of policy is not perpetual permission |
| Publisher signature | Publisher reuse purpose, key/issuer/enrollment identity, parent hash and all consumer fields; TTS observation key cannot sign an accepted reuse association |

Pure validation takes the parent and issued context from **independently supplied
expected records**, not just IDs/hashes inside the proposed association. Require
same owner/project/page/source/split and actual audio, matching speech semantics,
explicit consumer Job association and correct issuer purpose. A bare signed TTS
observation is insufficient for accepted final-AAC reuse; retain it as history
until P3 finalizes/qualifies a receipt. Same bytes/text in unrelated Job B do not
create a binding: B must be captured, owned, explicitly authorized and accepted
by the future restricted publisher. Same use UUID/payload is idempotent; same
UUID with a different consumer/parent/payload conflicts. Chaining/cycles, absent
parents, unqualified parents and changed scope are rejected initially.

P1 covers this structure and parent/consumer constraints in pure tests. P2 can
observe real original synthesis but cannot certify reuse. P3 may introduce a
bounded immutable use table after actual parent publication/qualification exists;
P4 must independently validate the use and current policy before private
consumption. A live current lookup remains required after issuance and pinning.
No P1 row creates an accepted production reuse event, even if its structure is
valid. No new Job, worker task, queue, audio copy/cache or synthesis is needed to
represent a use.

### 2.4 Page presentation and caption binding

Define pure `PagePresentationBinding`: existing owner/project/page/source/split;
consuming Job/input manifest/full source revision; presentation/use UUID; order;
scene/frame identity, authored subtitle display chunks, local cue payload/hash,
exact audio identity/duration/window, caption policy/version and video recipe/hold.
Preserve saved absolute timing separately where the existing contract requires it.
It records a presentation, not engine execution. Its speech reference points to
the original authenticated parent, with a distinct authored-chunks hash.

Subtitle chunks are never automatically engine chunks. With identical actual
speech, regrouping subtitles changes caption/presentation identities, not speech
identity. Local cues still need exact authorized text, audio/window/allocator
binding and bounds; formatting is not permission to rewrite narration or captions.
A changed TTS segmentation/pause plan **does** change speech semantics even when
the concatenated spoken string is identical. This does not imply chunk WAV reuse.

Current `SegmentAudioProof.baseline` includes origin Job/run/manifest/order and cue
hash; `SegmentRequest` separately binds current snapshot order and exact page tuple.
Current `_request` constructs `frozen_baseline.v1`; `result_allocator.v1` exists
in pure validation but has no production reservation bridge. P1 must **not**
mutate an old proof/cue hash or relabel its baseline as consuming Job B. Reuse
with unchanged eligible cues can retain the original proof; changed captions
require later independently sealed presentation acceptance. P4 must retain all
existing v1 checks and establish any additional proof/parent/cue association
without inventing synthesis. If that cannot fit unchanged contracts, stop that
later extension for schema review. Until then deny the private request, rather
than resynthesize unchanged speech to satisfy presentation bookkeeping.

## 3. Existing fields and where authority comes from

Categories: **I** existing identity, **M** useful mutable metadata, **F** new
immutable captured fact, **O** future authenticated observation, **C** future
current authorization. I identifies an object; it does not attest speech.

| Field | Category / source | P1 treatment and later authority |
|---|---|---|
| Owner/project/Job primary keys and Job purpose | I; server DB rows, `Job.project` can become null | Reference existing IDs, no duplicate Job/status. F capture records their exact checked relationship; later C checks must find permitted live objects |
| TranscriptPage ID and page key/source/split | I + M; project/key uniqueness, mutable fields/deactivation | Optional existing page ID is a locator; F exact tuple is required, including zero. Draft/new pages can lack a final row ID; no fallback from order |
| LessonSegment ID/order/audio path | I + M; order-keyed mutable derived rows | Optional diagnostic locator only; do not use as producer identity, FK authority or immutable page association |
| `audio/slide_n.mp3`, part MP4 and sidecar paths | M; mutable project workspace | Optional bounded diagnostic references, never private import handles or expected-hash authority. O signed original bytes and P3 F eligible assets required |
| Job-scoped current render manifest | M, despite being Job-scoped | F capture the exact relevant input payload/hash once under authority; do not sign the subsequently changed file or recopy all application state |
| Existing narration/settings/chunks | M; saved rows/draft/task descriptors | F exact protected capture, planned preparation/profile and distinct authored chunks; O actual consumed input/engine calls in P2 |
| Normalization/provider/fallback metadata | M; result dictionaries/sidecar | Diagnostics and candidate inputs. O actual provider/voice/resources/coverage must be observed, not inferred from `cached` or HTTP success |
| VoiceProfile/model/sample/lexicon names | I + M | F enrolled required profile; O same loaded bytes/resources actually consumed; C consent/revocation/profile compatibility |
| Original MP3/WAV and final AAC association | O then F immutable signed lineage | No new storage in P1; P3 bounded verified publication once per actual derivation, no preview-time MP3 certification |
| Scene/selection/approval/renderer/frame | I + M, or existing frozen Phase 3B records | F presentation reference only; current design/evidence revocation C remains separate. No effect on speech unless explicitly consumed as speech input |
| Current owner/source/profile/issuer/receipt permission | C | P1 models control semantics and real lock tests only. Future authoritative reads cannot be supplied by the browser or frozen forever in a receipt |

Do not duplicate mutable page/Job tables, whole source documents, model weights
or voice samples into P1 tables. Capture only the bounded immutable input facts
needed to explain/verify an original execution; retained audio/sample objects are
future external references. Changing or deleting application metadata never
rewrites signed capture history.

## 4. Minimal non-active P1 database schema

Introduce **six tables**, names below fixed for the P1 proposal, with one additive
migration sequence selected from the actual current migration graph. Register
models explicitly with Django; no import discovery trick. All production tables
start empty. Test-only fixtures live in disposable test databases. Existing
Job/Project/TranscriptPage/LessonSegment fields, statuses and delete behavior stay
unchanged. No acceptance/runtime API is exposed.

| Table | Why existing records are insufficient / minimum contents | Mutability, writer and relationships |
|---|---|---|
| `AudioProducerAuthority` | Operator enrollment statement: UUID, issuer/key ID, allowed purpose/environment, public key, approved image/resource profile hashes, validity window and operator-signed enrollment digest/bytes. App profile rows do not establish a signing authority | Immutable; operator enrollment role only. New key/profile/enrollment creates a new row. No app FK; controls reference it. No private keys in DB |
| `AudioProducerExecution` | UUID; owner/project/origin Job IDs/purpose; exact stable page/optional page ID; source capture and planned speech/context payload/hash. Ordinary Job lacks immutable consumed inputs | Immutable; future restricted publisher capture role. Existing IDs are scalar historical references, not new entities or cascading FKs. One row per logical original synthesis invocation; reused audio creates no such row |
| `AudioProducerAttempt` | UUID; execution FK; generation, nonce, issued-context event reference/hash; exact signed original observation/outcome and actual speech hash/output refs. Task IDs do not fence/reconstruct inference | Append-only observation/outcome row, inserted after an issued attempt resolves; never an initially blank row patched to success. Publisher ingestion role only. PROTECT/NO ACTION internal FKs to execution, relevant issuer enrollment and issued event. Outstanding attempts remain recorded by control/event, not a fake observation |
| `AudioProducerReceipt` | UUID; execution/accepted attempt FKs; scope/proof/outer-envelope hashes and exact canonical signed bytes; fixed original/sample/AAC/cue refs and qualification binding. Existing sidecars/results cannot supply independent expected proof digests | Immutable **original producer receipt only** in P1 schema; publisher role only. One accepted original per execution; accepted attempt and issuer/context references PROTECT/NO ACTION. No reuse subtype or mutable completion status |
| `AudioProducerControl` | One control row targeting exactly one authority, execution or receipt; monotonic epoch/current event pointer, deny/retired-valid policy, or execution generation/nonce/deadline/accepted-receipt pointer. Existing Project/Job state does not hold producer authorization/fences | Only mutable projection. Typed nullable internal FKs plus exactly-one-target CHECK and unique target; no arbitrary object-kind/string targets. Updates only through narrow role-scoped SQL routines; direct runtime UPDATE denied. It is not a second Job status machine |
| `AudioProducerControlEvent` | Immutable event UUID/control FK/sequence/previous digest, bounded event type and signed authorization or issued context hash/bytes; observed policy transition/attempt issuance | Append-only via same routines that change control in the same transaction. Unique `(control, sequence)`; PROTECT/NO ACTION. Allowed event set limited to enrollment/control/revocation, attempt issue/invalidate and original receipt acceptance; no general event bus |

### Why reuse and publication tables are deferred

Do **not** add `AudioProducerUse`, `PagePresentation`, generic Artifact/Edge,
publication outbox, transfer lease, audio cache or per-chunk tables in P1.
Validated reuse/presentation structures are necessary now to prevent false origin
semantics, but production acceptance and physical durability evidence do not yet
exist. P3 may add one immutable `AudioProducerUse` referencing an accepted
original receipt and an explicit consuming Job/presentation; fields are section
2.3, publisher writer, protected internal parent, scalar historical external IDs.
It must not allocate a new producer execution or copy audio. No chaining initially.
This is a deferred concrete relationship, not a generic artifact graph.

Publication/artifact-intent records are P3 work because fsync/transfer leases,
quota and crash reconciliation need actual object operations. Fixed bounded
receipt object references may suffice for inventory; add a table only for the
specific publication/lease operation that cannot be represented safely. Current
VisualSegmentRun dispatch intent and RenderFollowUpIntent are not producer-vault
publication intents and must not be repurposed. No second queue follows.

### Foreign keys, deletion and immutable snapshots

New immutable records use existing owner/project/Job/page **IDs as captured scalar
references**, with live existence/relationship checks in future privileged capture
and use validation. This is deliberate: `Job.project` is SET_NULL and page/segment
state can disappear/change; a cascading FK would erase evidence, SET_NULL would
rewrite signed identity, and PROTECT to ordinary rows would change normal deletion
behavior. No FK from the ledger to those ordinary tables is introduced in P1.
This does not make a captured ID trustworthy by itself; only authoritative
capture/signature and live scope validation provide that trust.

FKs between new ledger tables are actual protected DB relationships, including
receipt→attempt→execution and control/event links. Insert checks ensure execution,
issuer purpose, scope and generations agree across those rows; do not rely on
application `clean()` alone. A future accepted use must reference its real parent
row/digest and scope, not any content-address match. Cyclic control/event/receipt
references can be nullable only for an initial disabled control/new-row atomic
setup, never as an accepted parent bypass; check the completed state at commit.

Hard deletion of origin or consuming Job, project or owner makes dependent **new
private use unavailable** by conservative live policy. Deleted/inactive/replaced
page mapping cannot be repaired from LessonSegment order. Signed history remains
unchanged; no new access is derived from detached IDs. Privacy erasure/redaction
and object retention need a later explicit policy, not cascading authority deletion
or indefinite audio retention by accident. P1 tests deletion semantics without
changing existing application delete handlers. References must not resurrect
permission if an administrator restores old rows; new enrollment/current checks
and recovery fences remain required later.

### Constraints and replay state

- UUIDs are immutable event identities; pure builders/verifiers check stored
  digests against exact canonical bytes, while DB insert constraints/routines
  bind scope/index/reference columns to their payload values. The SQL layer
  enforces relationships and immutability; it does not verify Ed25519, actual
  speech or a privileged publisher's honesty. That publisher is in the stated
  trust boundary and must verify before inserting. Indexes use
  explicit nonnegative integers, excluding bool. Source/split/order bounds remain
  compatible with existing `_page`/`index`; zero is valid. Wire DB IDs are canonical
  positive decimal strings fitting signed bigint, without Unicode digit aliases.
- Unique execution UUID, observation UUID, receipt UUID, `(execution, generation)`,
  issued nonce and issued-context digest. Initial maximum three issued attempts per
  execution, as previously proposed. Accepted original receipt unique by execution
  and accepted attempt. Equal speech/audio hashes are **not** global uniqueness.
  Attempt-issuance events have typed execution/generation/nonce/context-digest
  columns, with uniqueness and a CHECK requiring them only for issuance events;
  historical nonce uniqueness is not limited to the current control projection.
  An attempt references exactly that issued event and can resolve it only once.
- Attempt issuance locks its execution control, appends the signed issued-context
  event and advances generation/nonce/deadline atomically. An unresolved earlier
  grant stays history; late generation cannot be accepted after invalidation or
  newer grant. An observation row binds exactly its issued event/nonce/context.
- Same observation/receipt/use ID with identical canonical bytes is an idempotent
  ingestion result; conflicting bytes fail. A fresh actual inference needs its own
  permitted generation. HTTP or broker task IDs are not this authority. P1 tests
  these transitions with fixtures; it adds no synthesis HTTP idempotency/cache.
  Expired/unissued nonces cannot authorize new observations. Returning an already
  accepted identical historical record does not renew a context or permission.
  P2 ingestion must authenticate/record timely observation before later full-Job
  qualification; P3 can qualify that immutable observation later only under current
  controls. Producer-reported timestamps alone cannot cure late/unobserved ingress.
- Acceptance locks current authority, receipt-parent controls where relevant and
  execution control, rechecks current generation/policy and appends receipt/event
  plus its control link atomically. No UPDATE/DELETE of observation/receipt bytes,
  even by the publisher role. Revocation appends an event and advances epoch,
  never changes what the producer said. Missing control is deny, not permissive.

## 5. Signature, parsing and permission boundary

Pure contracts: `ProducerContext`, planned/actual `SpeechSemantics`,
`OriginalObservation`, original receipt envelope containing unchanged
`SegmentAudioProof`, `AudioReuseAssociation`, `PagePresentationBinding` and typed
operator/control statements. Original and reuse contracts are distinct; a reuse
signature can never be accepted as an observation or original receipt.

Use the following new version/domain names in P1; none replaces an existing
Phase 3B schema. Nested structures do not need independent signatures when the
signed parent covers their canonical payload/hash.

| Structure / schema and hash domain | Permitted signature purpose |
|---|---|
| `production-producer-enrollment.v1` | `operator.enrollment.v1` |
| `production-producer-execution.v1` | Nested immutable capture, covered by issued context |
| `production-producer-context.v1` | `publisher.context.v1` |
| `production-speech-semantics.v1` | Nested planned/actual projection |
| `production-tts-observation.v1` | `tts.original-observation.v1` |
| `production-audio-receipt.v1` | `publisher.original-receipt.v1` |
| `production-audio-reuse.v1` | `publisher.reuse.v1`, pure validation only in P1 |
| `production-page-presentation.v1` | Nested presentation projection |
| `production-producer-control.v1` | `operator.control.v1` for operator instructions or `publisher.attempt.v1` for issued-attempt instructions, strict disjoint event allowlists |

The scoped speech hash uses `production-scoped-speech.v1`; exact signed
observation/receipt/use envelope hashes have their own respective schema domains.
Control events additionally hash their sequence/previous-event reference and
authorized instruction; DB acceptance events need no fictitious TTS signature.

Use Ed25519 with fixed algorithm and purpose domains: operator enrollment,
publisher context, TTS original observation, publisher original receipt and
publisher reuse. Producer and publisher keys are distinct. Context/receipt/reuse
may use one publisher key only with explicit separate purpose grants/domains.
Registry entries and their independent operator root are server dependencies,
never keys/URLs/JWKs embedded by a caller. A test registry has a different
environment/purpose and cannot validate production acceptance. No fixture switch
in runtime settings, ordinary endpoint or production trust lookup.

The common wire envelope has exactly `schema_version`, `purpose`, `environment`,
`audience`, `issuer_enrollment_id`, `key_id`, `payload` and `signature`. UUIDs use
canonical lowercase spelling; key IDs are bounded opaque registry lookups. Public
keys are 32 bytes and signatures 64 bytes, encoded as unpadded base64url with
canonical decode/re-encode validation. The signed bytes are the fixed prefix
`talkingslides-producer-signature.v1\n` plus canonical JSON of the entire envelope
**except** `signature`; the payload hash uses its fixed new schema domain.
Purpose/version pairs and payload fields are strict allowlists, not extensible
arbitrary metadata. Enrollment statements use an independently configured operator
root; embedding an enrollment/key inside a message never bootstraps its trust.
For operator enrollment itself, the envelope's issuer/key reference resolves an
already configured operator root, not the enrollment row being proposed.

Use existing `core.visual_design.schemas.canonical_json` for deterministic UTF-8
JSON and **new** content-hash domains; do not normalize exact authored Unicode or
mutate existing canonicalizers. Sign a versioned prefix plus purpose, environment,
issuer enrollment/key ID and canonical payload bytes; the signed envelope includes
those fields so changing any of them fails. Reject duplicate/unknown keys,
noncanonical bytes, nonfinite numbers, unknown versions/purposes and invalid UTF-8.
No claim of RFC 8785 compliance is implied by the existing serializer.

New protocol controls use normalized units and canonical decimal strings in the
effective profile (neutral `1`, `1`, `0` for speed/pitch/gain); requested raw values
are separately retained. Projecting to existing proof v1 uses its existing numeric
types unchanged. Integer times are milliseconds, not floats. Configuration blobs
are immutable hash references to versioned canonical manifests, not unbounded
provider JSON. P1 fixtures must fix exact bytes/hashes and compare supported API
and worker Python versions. Do not claim deterministic speech waveforms.

New envelope budgets: at most 1 MiB canonical bytes including bounded nested
structures; depth ≤20, node count ≤30,000, array/engine-call count ≤256; explicit
text and signed-artifact bounds no larger than existing segment limits. Subtitle
array remains limited by existing v1 checks (128 chunks). No embedded audio/model
bytes, arbitrary URLs or filesystem loading in pure verification. Oversized
envelopes fail instead of increasing existing limits. Decode/signature API has no
filesystem/network/ORM effects and returns a validated statement, **not an access
grant**. `SegmentAudioProof` still requires the independently expected digest.

`services/api/requirements.txt` does not declare an Ed25519 library directly.
Implementation must inspect the resolved dependencies and explicitly constrain a
maintained provider if necessary; a narrow justified crypto dependency is the only
potential P1 runtime dependency. Never implement Ed25519 manually or assume a
transitive package is a packaged API/worker contract. This design installs none.

### Roles and real PostgreSQL gates

| Role | Allowed P1 capability, exercised only in isolated schema tests |
|---|---|
| Non-login ledger object owner / separate migration operator | Own/alter new tables, triggers and functions. Trusted administrative boundary; never inherited/settable by application logins |
| Operator enrollment/control role | Insert immutable issuer grants and execute issuer/profile revocation routines. No TTS/publisher observation signing key or public endpoint role |
| Restricted publisher role | Bounded execution capture, observation/original receipt INSERT and execution-control routines; no issuer enrollment or immutable-row update/delete. A compromised publisher is inside the documented trust boundary |
| Dedicated reader role | Minimal read views plus lock/check function EXECUTE; no direct control UPDATE or table write. No ordinary production role membership is installed by P1 |
| Ordinary API/worker role | No INSERT/UPDATE/DELETE/TRUNCATE/DDL or privileged role membership on ledger. No publisher/operator keys. Future read access, if needed, must be explicitly scoped; do not silently grant current API writers authority |
| TTS process | No platform DB credentials. Later signs only original observations over authenticated contexts |

Role provisioning/ownership is explicit operator/test setup, not application
startup or an auto-migrator using public-service credentials. Migration/schema
tests cover SQLite structure where supported, but **PostgreSQL privilege and
lock acceptance is mandatory and cannot be replaced by SQLite or skipped green**.
Production-like PG acceptance requires the actual ownership/grant setup, including
denied owner-role inheritance, schema/function replacement and trigger bypass.
A plain schema migration by a broad app owner is not independent authority.

A SELECT-only reader needs an explicit lock primitive rather than a broad UPDATE
grant. Proposed narrow SECURITY DEFINER function locks/checks typed controls for
the supplied scope and expected epochs for the duration of the caller transaction;
it does not accept a claimed permission Boolean or caller time. Grant EXECUTE only
to the dedicated reader, with fixed trusted search path, schema-qualified objects,
no dynamic SQL and PUBLIC EXECUTE revoked. PostgreSQL documents extra privileges
for row locks and safeguards for definer functions. [PG1, PG2]

Use one lock order: owner → Project → source/voice/design controls → producer
authority controls sorted by ID → existing parent receipt controls sorted by ID →
execution control → ordinary Job → private Run. P1's new-control-only tests use
the same subsequence; it does not patch existing private transactions. Global
issuer revocation locks only its authority control, not Project afterward.
Per-receipt revocation takes authority then receipt. Execution retry takes
authority then execution; new unexposed receipt/control rows may be inserted in
the same transaction, without taking a reverse-order lock on a visible parent.
Bound lock/statement timeouts, rollback-safe transitions and missing-row denial.

PG mutation protection must cover SQL and bulk operations, not just model methods.
Revoke direct privileges and deny immutable UPDATE/DELETE plus statement TRUNCATE
using appropriate triggers. Row triggers alone do not cover TRUNCATE. [PG3]
Only narrow routines mutate controls; direct table UPDATE stays denied even for
reader/publisher logins. No function trusts the supplied owner ID as browser
authentication: later callers must establish the session owner and source scope.
P1 functions enforce ledger relationships/epochs only, with no production route.

## 6. Regeneration matrix — future behavior, not P1 execution

Keys: **S** speech semantics/scoped identity, **A** authenticated audio derivative,
**C** captions, **V** visual/frame, **P** presentation/video recipe, **L** lesson
sequence/packaging, **U** use binding, **Q** current policy/attempt fence. Full
lesson assembly below concerns ordinary lessons only; private previews never
assemble/publish a lesson. Reusable narration does not imply reusable encoded part.
Where new speech is required, only the separately authorized ordinary producer
path may generate it; the private reuse-only workflow returns ineligible and
cannot invoke TTS as a repair or fallback.

| Change | Speech synthesis / speech reusable? | Visual regeneration? | Page video / encoded-part reuse? | Lesson assembly? | Keys / current versus future |
|---|---|---|---|---|---|
| Visual-only | No; eligible existing speech reusable | Affected frame yes | Recompose affected video; old part not equivalent | Yes if updating ordinary lesson | V/P/U/L; selected reuse exists; auto/draft fine routing deferred |
| Narration-only | Yes for changed speech; old speech not a match | Only if grounding/layout depends on change | Recompose affected part and cues | Yes; later offsets change without later TTS | S/A/C/P/U/L; selected narration-only exists with restrictions |
| Subtitle presentation/grouping only | No if actual engine plan unchanged; audio reusable | No unless captions actually painted in scene | Sidecars only may reuse part; hold/burned captions require video change | Caption/packaging update; not necessarily MP4 concat | C/P/U/L, S unchanged; caption-only routing/acceptance deferred |
| Actual TTS chunk/pause plan | Yes for changed plan; no assumed chunk cache | Independent frame reusable | New audio/video/cues | Yes for ordinary output | S/A/C/P/U/L; plan identity P1, observation/routing later |
| Page reorder | No unless expanded spoken context changes | No unless visual includes order-dependent labels | Reuse exact compatible parts by stable tuple, not filename | Yes, reorder and absolute cues | L/P/U; S only for actual spoken-context change; structural optimization later |
| Add/remove page | New narrated page yes; removal no speech | Added/changed visual only | New page part; existing compatible parts reusable | Yes | New scope/S/V plus L; current structural/full behavior preserved |
| Source split/re-split | Changed speech partitions yes; exact unchanged tuple/plan may reuse | Only affected grounded scenes | Changed split parts/cues; no source-index-only reuse | Yes | Stable mapping/S/C/V/P/L; correct zero handling prerequisite |
| Source index zero | No operation by itself; identity must be preserved | No by itself | No by itself; mismatch denies reuse | No by itself | Scope exact; P1 strict tests, legacy builder fix deferred |
| Voice change | Changed consuming profile requires new speech under initial strict policy | Independent visuals reusable | Affected parts/cues | Yes when ordinary content updated | S/A/Q; selected global closure is future scheduling work |
| Voice sample change | Same rule; same voice ID/path is insufficient | Independent visuals reusable | Affected parts/cues | Yes if updating lesson | S/A/Q; actual sample observation P2, profile gate P4 |
| Model/config change | New required profile invalidates dependent speech; unaffected profiles reuse | Independent visuals reusable | Affected derivatives/parts/cues | If output changes | S/A/Q; actual loaded identity P2, routing later |
| Pronunciation/preparation resource | Recompute/compare consumed profile; changed relevant plan requires speech; old required profile denied | Reuse independent visuals | Affected parts/cues | If output changes | S/A/Q; initially all consumers of changed resource affected, no invented term-level selectivity |
| Missing/corrupt reused audio | Exact immutable copy may be recovered/reverified; otherwise ordinary authorized page synthesis or private denial | Valid frame reusable | Cannot reuse invalid bytes or part as proof; rebuild necessary derivative only | If ordinary result changes | A integrity/Q, not a new semantic event by itself; no P1 I/O |
| Expired/revoked authority/consent | Deny private reuse/acceptance; no automatic TTS repair | No repaint fixes permission | No authorized private video reuse/delivery | No private assembly; ordinary policy unchanged | Q changes; history/S stay immutable; live enforcement P3/P4/E1 |
| Interrupted synthesis/retry | Actual new inference gets fresh allowed generation/nonce; ingestion replay does not synthesize | Reuse independent frame if valid | Only current accepted audio/result wins | Only qualified ordinary result | Q/event/audio identity; protocol/PG tests P1, actual process tests later |
| Ordinary selected | Currently may reuse target speech or visuals; fallback can TTS whole target | Per selected task | Merge existing parts; not a universal fine scheduler | Current selected finalizer assembles | Existing fingerprints unchanged; future dependency closure/actions separate |
| Ordinary auto | Currently normal synthesis/render for dirty pages; unchanged pages get no page task | Dirty page generally rendered | Dirty artifacts recorded; baseline final retained | Not by dirty-only callback | Existing behavior; P1 grants no new done-Job meaning |
| Ordinary full/render-required draft | Currently synthesizes/renders all pages | Currently all pages | All page outputs, then finalization | Yes | Existing behavior; do not change full semantics in P1 |
| AI design select/approve/restore | No speech event if exact speech remains eligible | Selected scene/frame changes; approval is separate current control | New private request/result when allowed | None in private Phase 3B | V/P/U/Q; evidence revocation denies; full source revision still frozen/live checked |

Optional chunk-audio caching remains a later measured optimization. Persistent
authenticated lossless pieces, context/prosody, boundary churn, pauses, complete
coverage, join/encode policy and retention must be designed first. Page-level
reuse is the initial useful unit; no P1 WAV cache or inference benchmark.

## 7. P1 implementation surfaces and exclusions

Later authorized P1 may change only:

- New `services/api/core/visual_design/producer_contracts.py` for pure structures,
  hashes and signature verification, with no runtime registry defaults/I/O.
- New `services/api/core/audio_producer_models.py`, explicitly imported/registered
  from `core/models.py`, or the same six model declarations directly in that file;
  one location, not duplicate models. Add the next additive migration(s) and
  narrowly scoped PG triggers/functions for new objects only.
- New pure tests such as `tests/test_audio_producer_contracts.py`,
  `tests/integration/test_audio_producer_schema.py` and
  `test_audio_producer_postgres_roles.py`, with explicit test-only keys/registries.
  Extend existing suites for regression assertions rather than copying them.
- Narrow operator/test role SQL under a clearly named new `infra/sql/` file and
  test setup; no automatic role/issuer provisioning from ordinary startup.
- A justified direct crypto requirement if needed, and narrow CI PG-role test
  setup in `.github/workflows/ci.yml`; no GPU/model download or new production
  service/queue. Update implementation docs after actual results are known.

No P1 change to `tasks.py`, `tts_client.py`, `tts_service/main.py`, preprocessing,
per-page storage, render scheduling, LessonSegment/TranscriptPage behavior,
existing pure v1 contracts/hashes, `ProducerTrustSource`, private request/claim/
delivery wiring, deployment readiness, Docker entrypoints, Studio, publication
or ordinary deletion routes. No accepted records, real keys, enrollment rows,
worker, audio cache, filesystem scan/backfill, fixture production trust or GC.

## 8. Acceptance and reconciliation of T01–T22 with A01–A32

All following tests are **requirements for future implementation**, not results
of this design task. P1 covers structural/cryptographic/database semantics only;
the later column identifies the actual evidence still required.

| P1 gate | Concrete assertion | Original cases / later execution |
|---|---|---|
| N01 | Strict canonical/signature tests: altered purpose/environment/issuer/header/payload, duplicate keys, wrong key/algorithm, Unicode/number/limit cases rejected; production registry rejects test/engineering purposes | A01,A02,A32; T19. Real key installation/packaging P2/E1 |
| N02 | Visual/order/subtitle changes leave S/scoped speech unchanged when actual speech unchanged; order-expanded `Slide N` changes S; actual chunk boundaries/pauses and loaded controls/resources change S | T01,T03,T05,T08,T13,T21; A05,A09,A12,A16,A17. Scheduler/actual speech P2/P4/later dynamic work |
| N03 | Separate original observation and reuse contracts; reuse preserves actual provider/root IDs and cannot validate as fresh synthesis. Same semantics with two genuine attempts produces two event identities | T10,T19; A06,A19,A26. Actual no-extra-TTS instrumentation P2/P3/P4 |
| N04 | Reuse same stable page across authorized Jobs/orders, explicit consumer binding required. Wrong owner/project/Job, tuple/split, missing parent or same hash alone fails; zero/bool/missing/index confusion tested | T05,T06,T10,T19; A05,A06. Live Job capture/use acceptance P3/P4 |
| N05 | Caption-only proposal changes C/P, not S. Validate exact audio/cue/text/parent association; changed cue hash cannot mutate old proof or gain existing frozen-baseline eligibility | T03,T08; A13,A17. Measured allocator/private bridge P3/P4 |
| N06 | Requested-versus-actual model/sample/preprocessing/provider/controls and complete ordered coverage differ explicitly; unsupported fallback/filtered/dropped/recovered input cannot be a successful eligible statement | T07,T12,T13; A07–A12. Actual loaded-resource/engine coverage P2 |
| N07 | Ordinary paths/hashes/metadata and ID3 headers cannot construct authenticated authority; pure verification performs no file read/encoding. Detached/changed rows retain immutable history and cannot validate a new use from identifiers alone | T09,T11,T14,T16; A04,A06,A26. Byte replacement/native probes P3/P4 |
| N08 | All six tables empty after migration; schema/FK/unique constraints, immutable bytes and no cascade to ordinary delete behavior. Parent digest/scope/generation columns match payload, invalid relationships fail raw SQL | T10,T15; A03,A05,A06,A18,A19. Live publication P3 |
| N09 | Same observation/receipt ID+bytes idempotent; different bytes conflict; expired/unissued/replayed nonce or stale generation denied; no global uniqueness by text/audio hash | T10,T15,T16; A01,A18,A19. Broker/kill/reconciliation P2/P3/P4 |
| N10 | Revocation/required-profile epoch changes do not rewrite S/observation/receipt; fixture retired-valid issuance distinguished from compromised/expired permission. Captured old positive epoch cannot authorize later use | T07,T17; A07–A09,A15,A25. Real producer/current delivery lookup P4/E1 |
| N11 | Existing legacy/private golden hashes and literal closed gates unchanged; no hooks/routes/fixture key material added to runtime images, no default issuer grants or accepted records | T01,T02,T04,T20,T21; A20,A21,A26,A31,A32. Existing runtime regressions/later isolated acceptance still required |
| PG01 | Separate real PG connections/logins: ordinary API, worker and reader cannot INSERT/UPDATE/DELETE/TRUNCATE, COPY FROM, change ownership, disable triggers, replace definer function or SET ROLE to privileged owners; TTS has no DB login | A03,A32. Required P1, no SQLite substitute |
| PG02 | Correct operator/publisher role positive paths and cross-purpose negative paths; publisher cannot enroll authority or mutate historical evidence; reader gets lock/check EXECUTE without UPDATE | A01,A03,A06. Required P1 with test registries only |
| PG03 | Raw SQL/bulk UPDATE/DELETE and statement TRUNCATE protection, internal protected FKs and ordinary Job/page deletion history tested; control/event changes atomic or fully rolled back | A03,A18,A29; T14,T15. P1 DB semantics; real object durability P3 |
| PG04 | Two sessions grant/consume generations and accept conflicting receipts: current generation/nonce CAS and one accepted original win. Late worker grant fails, exact duplicate ingestion returns same identity | A18,A19; T15,T16. Real worker processes later |
| PG05 | Reader transaction holds shared authorization/control lock while revoker blocks; reverse ordering denies stale epoch. Rollback releases lock; connection pool does not retain authority | A15,A25; T17. P1 real locks; wire into claim/completion/stream P4 |
| PG06 | Fixed search-path/temporary-schema shadow attempt and PUBLIC EXECUTE test; caller cannot select arbitrary table/SQL, scope or claimed positive permission to elevate access | A03,A06. Required P1 |
| PG07 | Missing control/grant, expired issued context, cross-environment/profile and stale event-head transition deny; monotonic epochs/generation, previous-event references and commit-time completeness enforced | A01,A15,A18,A20,A25. Real enrollment freshness E1 |
| PG08 | Lock ordering across two owners/projects, issuer revoke, receipt revoke and attempt retry under bounded deadlines; no reversed lock dependency or privileged fallback | A03,A15,A18,A19; T14,T15,T17. Required P1 role/lock gate |

Complete phase allocation of the existing audit cases:

| Integration cases | P1 portion | Actual acceptance deferred to |
|---|---|---|
| T01,T02,T04 | N02/N11 identity/no-activation semantics | Existing selected behavior regressions; per-page dynamic routing separately authorized |
| T03,T08 | N02/N05 speech-caption separation | P3/P4 caption binding; future scheduler/video derivation |
| T05,T06 | N02/N04 explicit tuple and zero | Existing builder fixes and structural routing later |
| T07,T12,T13 | N02/N06/N10 resource/plan/coverage structure | P2 actual observation, P3/P4 eligibility; no chunk-cache requirement |
| T09,T11 | N07 untrusted/path-only denial | P3/P4 media validation and ordinary recovery regressions |
| T10 | N03/N04/N08/N09 original/use lineage | P3 accepted use; P4 private consumption |
| T14,T15,T16 | N07/N09, PG03/PG04/PG08 ledger scope/fences | P2/P3 actual interrupted Jobs; P4 private execution races; normal promotion separately reviewed |
| T17 | N10, PG05/PG07/PG08 | P4 live producer checks; E1/E2 actual delivery |
| T18 | Existing no-activation invariant only | P4 public-route regression and E2 actual proxy topology |
| T19 | N01/N03/N04/N07 and PG permissions | P2/P3/P4 actual producer/native association |
| T20 | N11, no private controller changes | Existing Run dedupe plus later P4 reuse acceptance |
| T21 | N02/N11 protected-context semantics | P2 ordinary empty/fallback compatibility, P4 exact narration |
| T22 | No readiness activation; N11 | E1/E2 built image/Redis/mount/runtime/proxy evidence |

Complete allocation of the production adversarial cases (same IDs retained):

| Production cases | P1 coverage | Later mandatory gate |
|---|---|---|
| A01,A02,A03 | N01/N08/N09; PG01/PG02/PG03/PG06/PG07 | P2/P3 authenticated ingestion and role deployment |
| A04,A05,A06 | N04/N07/N08; PG02/PG03 | P3 byte confinement; P4 exact live source/use |
| A07,A08,A09 | N02/N06/N10; PG07 | P2 actual loaded sample/model/resource, P4 current profile/consent |
| A10,A11,A12 | N06 structure and profile rejection | P2 real engine behavior and legacy compatibility |
| A13,A14 | N05/N07 processing references only | P3 actual WAV/AAC operations; P4/E1 runtime/packet-copy |
| A15,A16,A17 | N02/N05/N10; PG05/PG08 | P2 captured text; P3 captions; P4 revoke/source/current delivery |
| A18,A19 | N09; PG03/PG04/PG08 | P2/P3/P4 real crash/broker/worker tests |
| A20,A21,A22,A23,A24 | N11 no enrollment/activation; PG07 only epoch semantics | E1/E2 independent enrollment, time/image/mount/Redis/queue checks |
| A25,A26 | N07/N10/N11; PG05/PG07 | P3/P4 producer outage, real authenticated reuse and legacy denial |
| A27,A28 | Existing closed gates preserved only | P4 owner/public-route checks; E2 real slow/aborted proxy streams and FD limits |
| A29,A30 | PG03 DB rollback and fixed reference structure only | P3 publication/transfer crash tests; E2 capacity, inventory, backup; no P1 GC |
| A31,A32 | N01/N11; PG01/PG02 | P2 ordinary full/draft/partial compatibility; E1 packaging/enrollment |

Required future commands: new pure contract suite; Django checks/migration
consistency; new schema suite; dedicated real PostgreSQL role/lock suite with
required provisioning; existing `test_render_planner_foundation.py`,
`test_partial_render_manifest.py`, `test_partial_rerender.py`,
`test_visual_render_planning.py` and Phase 3B contract/control regression suites.
P1 implementation must record exact commands/results for its final tree. No
successful skip of the PG job establishes P1 acceptance; unavailable role-capable
PostgreSQL is an explicit blocker. No GPU, TTS calls or Docker media rendering is
needed to prove P1's contracts, nor can their absence be called P2/P3 acceptance.

## 9. Stop conditions and future sequence

Stop P1 rather than broaden it if canonical bytes/purpose/key separation remain
ambiguous; DB roles cannot be isolated/tested; source zero/parent/scope relationships
cannot be expressed without positional fallback; actual observation is being
invented from metadata; immutable evidence needs an update/cascade to work;
lock/privilege tests fail; existing v1 golden hashes would change; crypto support
requires unsafe custom code; or any proposed hook changes runtime behavior.
Report actual blockers. Contract test fixtures are not real producer evidence.

After P1 only, independently scoped tasks are:

1. P2 observe actual new synthesis through existing normal/narration-only call
   sites, immutable resources and complete engine calls. Reuse does not call TTS
   for a receipt. Name operator key/sample/consent owners before real enrollment.
2. P3 publish/qualify original audio once using signed source and approved WAV→AAC
   policy. Persist use/lease/intent records only when required by real operations.
   Initial successful-full-render qualification remains conservative; neither
   auto done status nor missing provenance forces full resynthesis for receipts.
3. P4 use genuine accepted parents and current policy in private proof/caption/
   reuse adapters. Keep existing source revision, attempt fencing, owner isolation
   and nonpublishing rules. Changed captions unsupported by strict contracts deny
   until a separately reviewed sealed-binding extension is accepted.
4. Separately authorize improvements to existing dependency planning, zero-aware
   mapping, per-page action choice, ordinary draft/auto behavior and immutable
   assembly boundaries. Reuse existing TTS, raster, allocator and FFmpeg helpers;
   preserve exact source-grounded visuals and current security. This is not P1–P4
   scheduling work and does not authorize Phase 3C or public release.
5. E1 independent expiring deployment enrollment; E2 proxy/capacity/reference-aware
   inventory/backup acceptance; then a separate rollout proposal. Passing tests
   or writing a valid ledger statement never activates private previews.

Open later decisions do not block this inactive P1 foundation: actual custody and
resource enrollment; explicit approval of pre-legacy-trim WAV derivative versus
exact ordinary trimmed narration; presentation/changed-caption acceptance;
selected/auto qualification; production topology/freshness/capacity and privacy/
retention policy. They **do** block their respective later phases and activation.

Design validation: the four requested documents were read; source conflicts
checked against current code and PostgreSQL primary references; document links,
source anchors, test-case allocation, hash/schema compatibility boundaries and
documentation-only diff scope checked. All acceptance tests above remain proposed.
No runtime or CI pass is claimed. The original integration review is retained
unchanged and this revision remains uncommitted/unpushed.

Primary PostgreSQL references for the proposed permission design:

- [PG1 — privileges and row locking](https://www.postgresql.org/docs/16/ddl-priv.html).
- [PG2 — SECURITY DEFINER safety](https://www.postgresql.org/docs/16/sql-createfunction.html).
- [PG3 — statement and row trigger behavior](https://www.postgresql.org/docs/16/trigger-definition.html).

**P1_REVISION_READY_FOR_IMPLEMENTATION**
