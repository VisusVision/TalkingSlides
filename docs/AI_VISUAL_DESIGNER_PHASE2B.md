# AI Visual Designer — Phase 2B

Phase 2B adds owner-private visual choices and a working preview inside Studio's
existing Intelligence → Visuals tab. It does not apply generated scenes to videos.
Baseline: `02aa14dad0d1dc5a4ac1c0fa2249bcd9b6e9f0c6` on
`feature/ai-visual-designer`. Tailwind 4 and Router/navigation security are retained.

## Storage and boundaries

`VisualDraftSelection` stores one current choice per project/owner/stable page key:
original or selected candidate, source revision/hash, plan/preview hashes, design
schema/template/renderer versions, `contains_text`, timestamps and optimistic
version. No SVG is copied into this record. `VisualDraftDecision` is append-only
select/reject/restore history with the resulting choice and monotonic version.
Candidates remain immutable. The additive migration is `0039_private_visual_drafts`.

Neither model participates in `Project.draft_data`. No visual API calls ordinary
draft save/promotion, changes `Project.updated_at`, touches active transcript/editor
scene fields, sets `visual_assets_dirty`/`render_required`, writes a render manifest,
creates a Job, or dispatches Celery/TTS. This also isolates selections from existing
render-job temporary state. Normal global Save and Save & Rerender do not apply a
visual choice. A regression actually promotes an ordinary draft and verifies that
it does not copy the selected SVG or rewrite narration.

A rejected candidate is retained. Rejecting the currently saved choice returns
that private choice to original; rejecting a different candidate preserves the
choice. Deliberate re-selection supersedes rejection. Restore original is an
explicit saved state and history event, not a fabricated replacement candidate.
Owner transfer does not expose the previous owner's private choices.

## Source revisions

The server adapter is explicitly versioned `studio-source.v2`. The Phase 1
`slide-source.v1` JSON schema and canonical hash format remain unchanged.

- Drop the broad Project `updated_at` from source fingerprints. Unrelated project
  timestamps and private visual-choice timestamps are not source content.
- Retain authoritative page content/identity, whole saved page structure, active
  page revision/lifecycle stamps, source mode, saved source-draft revision metadata,
  editor document and original asset identity. This conservatively invalidates
  decisions on ordinary source/draft revision changes, including delete/restore.
- Fingerprint original image bytes with bounded SHA-256 streaming (32 MiB limit),
  in addition to project-scoped reference/size/mtime. Same-size changes with a
  restored mtime are detected. Oversized or changing images are unsupported rather
  than silently treated as verified. No OCR/semantic extraction is added.
- `source_slide_index=0` and split index 1 remain exact integer identities.
- A visual-only decision changes only its own tables, so its source hash stays
  stable. No staleness comparison is bypassed. Old adapter-v1 candidates naturally
  become stale; there is no test-data compatibility migration or candidate rewrite.

Selection rechecks candidate owner/project/page, schema and renderer versions,
status, immutable plan and preview hashes, source evidence, SVG safety/geometry,
and byte-deterministic SVG re-generation. A forged authoritative value cannot
replace the saved source. Historical private previews remain authenticated and
can be viewed as historical even when stale; stale designs cannot be selected.

## API contract

`GET /api/v1/projects/{project_id}/visual-draft/?page_key={stable_key}`
returns `visual-draft.v1`, current source metadata, selection, selected candidate
metadata, rejected candidate IDs and the newest 20 decision events. An unsaved
original choice has version 0 and creates no database row. A deleted page can
still show its stale private choice/history to its owner; it cannot be selected.

`POST` to the same endpoint accepts only:

```json
{
  "page_key": "source-0-split-1",
  "action": "select",
  "candidate_id": "<candidate UUID>",
  "expected_source_hash": "sha256:<64 lowercase hex digits>",
  "expected_version": 0
}
```

Actions are `select`, `reject`, `restore`. Select/reject require candidate UUID;
restore must omit it. Unknown fields, browser-authored content and coerced boolean
or string versions are rejected. Only the project owner may read or modify choices;
staff status is not an override. Errors do not reveal foreign project/candidate
existence. Source/revision/integrity conflicts return 409; unknown/foreign candidates
return 404; malformed contracts return 400. Responses are private/no-store with
Authorization/Cookie Vary, nosniff and no-referrer headers.

Updates are atomic. Lock Project, then active-page rows in promotion-compatible
order, validate the saved source, compare the selection version, perform a CAS,
append one event and recheck the source before commit. No Project/page is saved.
An identical retry of the immediately preceding operation can reuse its result;
a request from before a later decision cannot overwrite it. Consecutive identical
operations do not duplicate events. SQLite lock conflicts return 409 rather than
claiming success; PostgreSQL concurrency is tested separately with real row locks.

The ordinary API authorization is checked again inside the transaction. No HTTP
asset URL from suggestion metadata receives a token directly: existing API helpers
construct authenticated local preview routes and create image blob URLs.

## Studio behavior

`VisualDraftControls.jsx` owns the separate private selection interaction;
`VisualSuggestionsPanel.jsx` retains original/candidate comparison and generation.
`visualPreviewHooks.js` shares authenticated image loading and object URL cleanup.

1. Generate a suggestion from saved Studio source.
2. **Use this design** stages a local choice and immediately loads its image into
   the working preview. It sends no selection/save/render request.
3. **Save visual draft** persists the private choice with expected source hash and
   version. Saved/unsaved state is explicit; reopening Studio restores the choice.
4. **Reject suggestion** records the decision without deleting the candidate.
5. **Restore original** stages the original preview; Save visual draft persists it.
6. **Regenerate** uses Phase 2A's bounded deterministic API. Identical candidate
   reuse is stated plainly; it is never randomized to simulate new content.

The working preview deliberately remains inside Visuals. It displays a complete
SVG as an `<img>` and never draws the transcript overlay on it. The central
renderable Studio scene and global dirty/save state are unchanged. This is a
specific Phase 2B limitation, preserving the separation required for future video
application. English, Turkish and Arabic messages use existing localization; controls
wrap on mobile, use native buttons/selects and keep nested Analysis tab behavior.

Unsaved narration/source edits disable generation and all decision writes. They
are not sent as visual evidence. Source conflicts keep the choice unsaved; Reload
visual draft refreshes the parent source listing as well as private state. Page,
project and revision switches clear local staging. Save before leaving the page/tab;
unsaved staging is not persisted across navigation. Request epochs, a synchronous
busy guard, scope-bound state and preview keys prevent old-page responses from
changing another page. URLs are revoked on replacement/unmount and late arrival.

## Verification

Executed checks and hosted results are recorded below after the final CI run.
Initial local checks: fresh npm installation; 342 frontend tests (48 files);
production build; both npm audits zero; Django system/migration checks pass.
Relevant backend tests include Phase 1/2A, new selections/concurrency and existing
ordinary-draft/promotion isolation. Browser acceptance uses hosted Chromium when
local downloads fail. Blocked browser launches are never counted as passed.

The CI adds `visual-drafts-postgres`, using PostgreSQL 16 and isolated test settings,
to verify actual simultaneous API updates. Existing backend, Docker, load, frontend
and audit jobs are not removed or weakened. No new application dependency is added.

## Limits and Phase 3 prerequisites

Real sources still provide authored prose rather than verified process/comparison/
numeric evidence. Text-only lessons can produce typography previews. Sources with
an original image normally conservatively return preserve_original; use Restore
original rather than pretending that a generated SVG exists. No Ollama, semantic
extraction, AI image generation, PPTX export, animation, audio or video work is added.
History responses are bounded to the newest 20 events; candidates/events remain in
private storage. Physical devices/Safari/Firefox are separate platform checks.

Before applying choices to video, Phase 3 must freeze the exact ordinary draft
revision, validate owner/source/asset provenance again, define completed-scene
semantics and text ownership, maintain split-page/subtitle mapping, reuse audio for
visual-only edits, and promote only the revision actually rendered after moderation.
Published playback must remain in place until its approved replacement is ready.
None of those renderer/promotion changes is activated here.
