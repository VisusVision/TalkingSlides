# AI Visual Designer — Phase 2A

## Scope and outcome

Phase 2A connects the deterministic Phase 1 prototype to saved lesson pages and the existing Studio Intelligence inspector. A project owner can select a page, generate a private suggestion, inspect its status/rationale/validation, compare previews, and return to the stored candidate later. This is **preview only**: no candidate is selected into a draft or applied to video.

Baseline verified before editing: `feature/ai-visual-designer` at `7c732ca59c697f78f2acdae015bf3bdbdddce1cc`. No intervening changes were present. Only this feature branch is updated; main/developer are not changed or merged.

## Existing infrastructure and integration boundaries

The implementation reuses these current paths:

- `core.drafts.get_studio_transcript_pages`: the same saved private-draft/active-page precedence Studio uses. `has_project_draft` determines whether a saved draft is dirty. Reading does not call `ensure_project_draft_data`, save, promotion or synchronization.
- `core.models.TranscriptPage`: stable `page_key`, source/split indices, exact `original_text` and `narration_text`, editor document and persisted revision timestamps.
- `core.views._studio_transcript_pages` and `_draft_page_response`: existing Studio page/scene presentation. The new component receives `selectedScene.page`; it never substitutes its numeric or temporary ID as a stable key.
- `core.views._is_project_owner` / `_can_edit_project`: authorization semantics were reviewed. New endpoints use an owner-filtered Project query, deliberately excluding staff, review and superuser editor overrides from private suggestion access.
- `core.views.TranscriptPageBackgroundImageView`: existing authenticated original-image delivery, including `draft=1` and stable draft page references. No new original-media storage/serving path is added.
- `core.storage_adapter.FilesystemStorageAdapter`: confined local original-asset revision lookup. The worker currently stores originals under `<project_id>/images/`; project uploads can also live under `uploads/<project_id>/`.
- `Studio.jsx` selected-page state, dirty flags and Intelligence feature visibility: only a small wrapper/prop integration is added. Existing Analysis content and callbacks remain mounted and intact.
- `StudioWorkspaceChrome.useStudioWorkspaceCopy`, `studioWorkspaceCopy.js`, Button and Dialog: existing localization, design tokens, keyboard/focus behavior and responsive controls.

The pure `core/visual_design/` package, its five fixtures and all Phase 1 tests remain unchanged. No changes to rendering, render planning, source extraction, draft saving/promotion, TTS, subtitles, moderation or playback are included.

## Read-only real-source adapter

`services/api/core/visual_design_source.py::build_studio_visual_source(project, page_key)` produces a `StudioVisualSource`, containing a Phase 1 `SlideSourceSnapshot` when supported.

The API resolves the Project by the authenticated owner before calling the adapter. It accepts a stable page key only, finds exactly one matching saved Studio page, and preserves integer zero values with explicit `.get` access. Page order, source slide index, split index and page key remain distinct. Negative temporary draft IDs are never used as snapshot identity.

Saved dirty private draft pages take precedence; otherwise active TranscriptPage rows are used. The source response labels this `source_mode=draft` or `active`. Missing or ambiguous page identities disable generation; no missing identity is synthesized.

The adapter retains exact display text and protected narration. It does not normalize, rewrite or save either field. The source revision hash includes:

- Adapter version, authenticated project identity, stable key and effective source mode.
- Project revision timestamp, selected active row revision/active state, and saved draft revision timestamp when relevant.
- Saved Studio page structure: keys, order, source indices and split indices.
- The selected page's exact display/narration, rich text, editor document and whiteboard flag.
- Relevant original asset reference identity, availability, file size and modification timestamp.

Transient timestamps produced by `build_draft_from_active_project` are deliberately excluded. Repeated reads/generation from identical saved state therefore reuse one candidate. The revision policy is conservative: even an unrelated Project save can invalidate a suggestion. Deletes, restores, changed structure, draft saves/discards and source revisions make old candidates stale. A restored identical page is still a new persisted revision.

Original file references are accepted only within the current project's local namespace, with storage-root and post-symlink confinement. Only existing PNG/JPEG/WebP files qualify for an original reference. The Phase 1 contract receives an opaque hashed `asset:` ID; neither filesystem paths nor source content are exposed through suggestion metadata. Original asset identity uses a stat fingerprint, **not a cryptographic checksum of file bytes**. Replacing bytes while deliberately retaining the exact size and modification timestamp is outside this fingerprint's detection guarantee. Remote/S3 originals and noncanonical legacy paths are not dereferenced.

### Unsaved changes

Browser-only edits are not accepted as source content. The API accepts only `page_key` and `expected_source_hash`. Client-supplied pages, narration, editor documents, evidence, source overrides and extra fields are rejected. Studio disables Generate while transcript, TTS or selected-scene edits are unsaved and asks the teacher to save or discard them using the existing editor controls.

Saving such edits is a separate, existing action. This feature never saves an unsaved working draft on the teacher's behalf. A listing still describes saved content while local changes exist, with an explicit warning. A POST whose source hash no longer matches saved content returns 409 and does not generate a candidate.

### Exactly what is trusted as evidence

Current persisted structures are trusted for **identity, authored text and asset provenance**, not for inferred educational relationships:

| Existing source | Use in Phase 2A |
| --- | --- |
| Project / TranscriptPage / saved draft fields | Authoritative identity, exact authored text, narration and revision input |
| `editor_document.scene.original_background_path` in the project's namespace | Original-asset reference/fingerprint only |
| Other editor document, scene and rich-text metadata | Revision identity only; never verified numerical or relational evidence |
| PPTX extracted text, speaker notes, pipe-separated table text | Authored prose only; no chart/unit/order reconstruction |
| Source-background details, flattened slide images | No semantic evidence extraction |
| Lesson Intelligence reports or Ollama suggestions | Not consumed by this adapter; future contextual hints must remain unverified |
| User-authored `evidence`, `table` or `verified` keys inside an editor document | Ignored as evidence |

The actual extraction paths (`services/scripts/pptx_extract.py`, `text_segmentation.py`, worker transcript synchronization) do not supply a complete, verified semantic chart/layout manifest. No current source is promoted to verified process/comparison/numeric evidence. Every real-source snapshot has `evidence=[]`.

Consequently **real lessons currently produce typography-only previews, preserve-original results, or explicit unsupported results**. Flowchart/comparison/chart rendering remains available in the pure package for explicit trusted structured inputs and its synthetic fixtures. This phase does not pretend that prose or an LLM has verified facts, units, values, process order or causality. It does not repair historical extraction index errors; it preserves the authoritative indices actually stored, including zero.

Unsupported contract content (for example unsupported characters, markup, oversized narration or invalid indices) is retained unchanged in the lesson and represented by a private unsupported candidate with a source digest and validation code. No rewritten/truncated replacement is substituted.

## Private immutable persistence

`core.models.VisualDesignSuggestion`, migration `0038_visual_design_suggestion`, adds one model:

- UUID identity, owner FK, project FK and stable page key.
- Effective source mode, source revision/hash and deterministic candidate key.
- Versioned suggestion envelope, generation status, creation time.
- Immutable plan/preview JSON, their hashes and validation errors.
- Strategy/rationale/template/schema/renderer versions and warnings inside the validated plan/preview payloads, avoiding duplicate mutable fields.

Generation states are `ready`, `preserved`, `unsupported`, and reserved `failed`. Known contract/layout limitations produce `unsupported`; unexpected request/server failures are surfaced as failures, not fabricated successful designs. Unsupported source contracts have no invented plan/preview hashes or executed renderer version.

SVG assets are bounded to the Phase 1 limits and stored inside the private database preview JSON, following the existing JSON report persistence pattern. This intentionally avoids a new publicly mounted media path, object-store ACL assumption or non-atomic file/row write. Metadata never includes SVG or physical storage paths. The dedicated preview endpoint returns the asset only after authorization and integrity validation. This storage choice is suitable for bounded prototypes; move large future assets to an explicitly private object store if needed.

Application saves of existing candidate rows are rejected. There is no candidate update/delete API. A database unique constraint on `(project, owner, candidate_key)` plus `get_or_create` deduplicates simultaneous identical generation safely. The candidate key incorporates source hash/mode and generator, plan, preview, template and renderer versions. A changed revision/version creates a new candidate. Direct SQL/bulk ORM writes by trusted administrators can bypass model immutability; preview delivery still checks contracts/hashes. This is not a database-trigger-based audit system.

Project/owner deletion cascades to the private rows, so no filesystem preview assets can become orphaned. Existing suggestions do not transfer to a new project owner: access filters require both the current project owner and the recorded candidate owner. History listings return the latest 20 candidates for the selected stable page. A later retention policy and pagination can extend this; no automatic deletion job was added.

Staleness is computed against the current saved Studio source on each list/detail request and exposed separately from immutable generation status. Historical validated previews remain privately viewable with a stale indicator/header. Original-image comparisons use the current saved original; original asset bytes are not copied/versioned in this phase, so an older preserve-original record is a historical decision rather than an archived copy of that original image. Incompatible versions are not served. Source changes while generation is in progress are detected by a fresh post-generation source read. This is sufficient for preview history; it is not an approval or publication lock.

## API contracts

All routes use current DRF Token/Session authentication and project-owner authorization. An unauthenticated request is rejected; a foreign project/candidate is returned as 404 without disclosure. Staff/superuser review access does not override this rule.

| Method / route under `/api/v1/` | Behavior |
| --- | --- |
| GET `projects/<project_id>/visual-suggestions/?page_key=<stable_key>` | Current saved source identity/support status and latest 20 private metadata records |
| POST `projects/<project_id>/visual-suggestions/` | Bounded deterministic generation; 201 for a new candidate, 200 with `reused=true` for an existing identical candidate |
| GET `projects/<project_id>/visual-suggestions/<uuid>/` | Immutable generation metadata plus current/stale/version-compatible status |
| GET `projects/<project_id>/visual-suggestions/<uuid>/preview/` | Private validated SVG; 404 when no supported SVG exists, 409 on stored integrity failure |

POST body:

```json
{
  "page_key": "s1-p2",
  "expected_source_hash": "sha256:<digest returned by the listing>"
}
```

Metadata includes `schema_version=visual-suggestion.v1`, candidate/project/page identity, source mode/revision/hash, plan/preview hashes, status, strategy, rationale, fallback reason, warnings/errors, compatibility, staleness/reason, contains_text, actual plan/renderer versions, creation time and optional private preview URL. `applied=false` and `can_apply=false` are explicit. The source envelope separately identifies whether an original image is available and provides an image-endpoint reference; a saved draft uses its stable key, an active original uses its authoritative DB image reference. This image reference is not snapshot identity.

Every response has `Cache-Control: private, no-store`, `Pragma: no-cache`, `Vary: Authorization, Cookie`, nosniff and no-referrer headers. SVG delivery adds a sandboxed CSP with no scripts/default resources/objects, and same-origin resource policy. Stored plan/preview contracts and hashes are checked again before serving; metadata is not sufficient authorization to fetch an arbitrary asset.

Generation has a separate 20 requests/minute/user throttle in addition to existing API throttles. It is not invoked during upload/extraction/rendering or through any render task chain.

## Studio workflow and security boundary

`VisualSuggestionsPanel.jsx` owns the new logic. `Studio.jsx` only wraps the existing LessonIntelligencePanel in `StudioIntelligenceTabs` and passes the selected page, project, revision, unsaved flags and review state.

- **Analysis** remains the default, with existing Intelligence content/callbacks mounted intact.
- **Visuals ✨** loads only when opened and uses the existing selected page; it is not another top-level workspace.
- Generate and Refresh are separate from Save Draft and Save & Rerender.
- Original/suggestion images, selected strategy, rationale, warnings, saved-source mode and current/stale state are shown. Stored-candidate selection means choosing which preview to inspect, never applying it.
- Preserved results show the original image when available and explicitly say that no replacement SVG was generated. Text-only sources show original authored text alongside the typography preview.
- Empty, loading, failed, unsupported, missing-page and private-review states are explicit. Unsaved working edits are distinguished from current saved-source status.
- Preview comparisons stack in narrow layouts. Images can be expanded using the existing image-only Dialog with keyboard focus/close support; they are not opened as executable SVG documents.
- Requests from an earlier selected page are ignored after a selection change. Private object URLs are revoked on changes/unmount; generation results never replace another page's state.

Browser asset URLs are constructed from the configured API base and project/candidate IDs, never arbitrary model/asset-supplied URLs. Requests send existing auth headers and disable caching. The response MIME type is checked. SVGs are displayed only through `<img src=blob:...>`, including the expanded viewer. There is no `dangerouslySetInnerHTML`, inline generated SVG, iframe, object, embed or executable SVG-document link. Metadata, rationale and authored text use ordinary React escaping. Original previews reuse the existing authenticated image route and restrict the accepted MIME type to PNG/JPEG/WebP.

Labels follow the existing document-locale hook and copy fallback. English, Turkish and Arabic UI strings are supplied; other existing Studio languages use the English fallback. Phase 1 generated rationale/validation messages remain English, and Phase 1's limited English/Turkish text repertoire is unchanged. Arabic UI availability does not imply Arabic SVG text support.

The tab follows the existing Intelligence inspector's capability visibility (`ENABLE_INTELLIGENCE` / existing deployment capability); it does not require Ollama. Read-only staff review explicitly hides private suggestion operations. The central Studio scene/video preview is untouched.

## Tests and measured results

Executed in a separate local test environment using Python 3.12.14, Django 5.2.18, SQLite test databases, and the existing frontend lockfile. No dependency file changed and no deployed database/lesson was accessed.

### Backend regression run

```bash
PYTHONPATH=services/api DJANGO_SETTINGS_MODULE=config.settings_test python -m pytest \
  tests/integration/test_visual_suggestions.py \
  tests/integration/test_studio_draft_isolation.py \
  tests/integration/test_studio_draft_management.py \
  tests/integration/test_draft_visual_assets.py \
  tests/integration/test_lesson_intelligence.py \
  tests/test_visual_design_contracts.py \
  tests/test_visual_design_planner.py \
  tests/test_visual_design_svg.py -q
```

Result: **162 passed, 63 subtests passed, 2 existing Django deprecation warnings, 15.62 s**. All 42 Phase 1 tests remain included and unchanged.

Additional transcript compatibility checks:

```bash
PYTHONPATH=services/api DJANGO_SETTINGS_MODULE=config.settings_test python -m pytest \
  tests/integration/test_transcript_page_actions.py \
  tests/integration/test_transcript_editor_pipeline.py -q
```

Result: **32 passed, 2 existing warnings, 4.05 s**. This brings the distinct executed backend tests to **194**. These tests mock relevant worker/media execution; passing does not establish real XTTS/FFmpeg/GPU behavior.

After final metadata adjustments, the dedicated Phase 2A suite was rerun: **30 passed, 2 existing warnings, 7.14 s**.

New backend coverage includes exact real-source conversion, zero/split identity, saved draft and temporary IDs, rejecting client content/evidence, owner/anonymous/staff/cross-project access, owner transfers, private SVG headers, repeatability/reuse, stale edits/splits/structure/deletes/restores/draft discard, hash preconditions, unsupported and unverified evidence, incompatible/tampered assets, immutable records and bounded history. A no-mutation test compares every Project, TranscriptPage and Job field before/after generation/preview, with network sockets and render task dispatch blocked.

### Frontend and build

From `services/frontend`:

```bash
npm test
npm run build
```

Results: **46 test files passed, 253 tests passed, 8.40 s**; production build succeeded in **1.46 s**. New component/API tests cover selection/page races, Analysis preservation, unsaved edits, original-image references, current/stale and unsupported states, loading/errors, localization fallback, image-only expansion, SVG/HTML escaping, MIME/auth boundaries and blob cleanup. Existing Studio Intelligence, transcript editor, workspace and all other frontend tests pass. The build retains the existing large-chunk/Browserslist warnings.

Also executed:

```bash
PYTHONPATH=services/api DJANGO_SETTINGS_MODULE=config.settings_test python services/api/manage.py check
PYTHONPATH=services/api DJANGO_SETTINGS_MODULE=config.settings_test python services/api/manage.py makemigrations --check --dry-run
```

Results: **0 system-check issues; no missing model migrations**. Test database creation applies the new migration successfully. `git diff --check` passes.

### Bounded performance

A separate synthetic in-memory SQLite run generated 20 distinct new candidates, timing source queries, contract conversion, planning/rendering/validation and DB insert: **3.028 ms median, 5.779 ms maximum**. This is an environment-specific engineering measurement, not a production latency SLA. The API tests additionally verify five repeated complete generation request cycles stay within a generous three-second local bound. Sources are limited to 1,000 saved Studio pages; Phase 1's payload/character/layout/SVG limits remain in force.

### Unavailable and remaining checks

A Playwright headless-browser installation was attempted, but its Chrome archive download was truncated/invalid. No browser E2E, screenshot or physical mobile visual check is claimed. Remaining local verification: open the real Studio workflow on desktop/mobile, check fonts and image sizes, and apply/verify migration 0038 on the intended PostgreSQL development database. Ollama, GPU, XTTS and actual video rendering were not run or required by this phase. The entire repository-wide backend suite was not run; the specified relevant suites above were run.

## Running locally after returning to the PC

Review/pull the existing feature branch without overwriting local work, install existing development dependencies, then apply migration 0038 using the normal development database configuration:

```bash
python services/api/manage.py migrate
```

Start the existing API and Studio services with the Intelligence inspector visible. Open a lesson in Studio, select a saved transcript page, open Intelligence → Visuals ✨, and choose Generate suggestion. Save/discard local edits separately first if requested. A text-only source can produce a typography SVG; a known original image will normally be preserved. Refresh/reopen the lesson to retrieve stored candidates. No design application or video replacement control exists in this phase.

## Future queue and resource isolation

Keep current bounded deterministic generation synchronous. Do not add Ollama to these requests. For future model-assisted generation, reuse existing progressive Intelligence provider resilience/scheduling behind a dedicated `visual_design` Celery queue with its own low-concurrency worker, bounded retries/timeouts, revision-based deduplication and stale-result cancellation. Separate its resource budget from render, TTS and avatar queues; coordinate Ollama/XTTS GPU use explicitly. It must be an independent teacher-initiated branch, never a render prerequisite/chord dependency. A timeout or provider failure must leave normal lesson rendering and published playback available. No queue, worker or task routing change is delivered here.

## Phase 2B plan: selecting into private drafts

1. Keep candidates immutable. Introduce a separate owner-authorized selection/decision contract keyed by project, stable page key, current source hash, candidate/version/preview hash, renderer version and contains_text. Reject stale, incompatible or unsupported selections.
2. Add Use this design, Reject, Regenerate and Restore original only when their safe server behaviors exist. Rejection/selection history belongs in separate decisions/draft pointers, not mutable generation status. Regeneration may reuse identical deterministic results; do not invent changes by adding a random nonce.
3. Update the editor's working preview immediately from a selected candidate, with a recoverable original. Preserve approved narration. Keep a future completed-design pointer in the existing private working draft; Save Draft persists it without rendering. Validate split/merge/delete/restore invalidation by stable key/hash.
4. Define completed-scene semantics before any video consumption: generated SVGs containing text must not receive the existing custom-background text overlay again. Phase 2B selection does not itself authorize scene application or change background modes.
5. Keep media integration behind separately authorized work: include visual/version identity in existing render fingerprints, retain eligible audio reuse, freeze the exact immutable draft revision rendered, and promote only that rendered revision after moderation. Current draft promotion is not assumed to provide this safeguard. Do not modify it in this preview phase.

No Use/Reject/Restore/Regenerate-variation controls are misleadingly activated now. Every response/UI explicitly remains unapplied. Existing Save & Rerender continues to operate only on the pre-existing lesson/draft workflow.

## Changed files

- `services/api/core/models.py`: append the private suggestion model only.
- `services/api/core/migrations/0038_visual_design_suggestion.py`: additive table/uniqueness/index migration.
- `services/api/core/visual_design_source.py`: read-only trusted saved-source bridge.
- `services/api/core/visual_suggestions.py`: deterministic candidate generation/reuse and metadata.
- `services/api/core/visual_design_views.py`: owner-only list/generate/detail/preview endpoints.
- `services/api/core/urls.py`: register only the new endpoints.
- `services/frontend/src/api.js`: dedicated private suggestion/original preview requests.
- `services/frontend/src/components/studio/VisualSuggestionsPanel.jsx`: nested tabs, previews, status and image-only expansion.
- `services/frontend/src/components/studio/studioWorkspaceCopy.js`: localized feature copy.
- `services/frontend/src/pages/Studio.jsx`: small selected-page/Intelligence wrapper integration.
- `services/frontend/src/components/studio/VisualSuggestionsPanel.test.jsx` and `services/frontend/src/api.visuals.test.js`: frontend regression/security contracts.
- `tests/integration/test_visual_suggestions.py`: new real DB/API tests.
- `docs/AI_VISUAL_DESIGNER_PHASE2A.md`: this report.

**Recommendation:** proceed to local UI/PostgreSQL verification, then a narrowly scoped Phase 2B private-selection implementation. Model-assisted evidence extraction and video application remain separate work.
