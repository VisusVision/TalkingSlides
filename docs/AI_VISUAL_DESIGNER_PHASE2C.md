# Phase 2C — Grounded Visual Intelligence

Implementation baseline: `9773826124e2612860b06d7f36f90a8cd36506a5`, on `feature/ai-visual-designer`. No video integration.

## Boundaries and source trust

`visual_design/grounding.py` is pure Python, independent of Django, settings, ORM, Celery, network and providers. It consumes the existing immutable `SlideSourceSnapshot`. `visual_design_source.py` still reads **saved** server-owned Studio pages, preferring saved private transcript drafts. Unsaved browser content never becomes evidence. Narration is copied verbatim and is never analyzed as a verified dataset.

Source extraction inspected: `services/scripts/pptx_extract.py` `extract_speaker_notes` reads PPTX notes or flattened text frames; `_extract_pdf_text` retains extracted text order, not a semantic reading-order guarantee; `_docx_split_slides` retains headings and serializes table rows with pipes in its fallback, but drops empty cells, and the preferred DOCX PDF route loses table semantics. There is no complete persisted PPTX table/chart/coordinate manifest. The adapter deliberately does not reopen source documents, OCR images, trust Lesson Intelligence prose, infer PowerPoint numbering metadata, or claim extraction preserved all original structure. Only explicit syntax in the **current saved display text** is usable. A visually numbered PPTX list whose numbering was lost is unsupported automatically.

Source-backed means faithful to an authored assertion, **not independently scientifically true**. Contradictory duplicate row/category/step identities are rejected. General factual contradictions cannot be established by these rules or by LLM self-review; teacher/source quality remains necessary.

## Deterministic grammar

`text-evidence.v1` recognizes a complete slide consisting of a heading and one supported structure:

* Process: a heading containing process/steps/procedure or Turkish süreç/süreci/adımlar/işlem sırası; 2–4 contiguous lines numbered `1.` or `1)` through N. Arrows mean sequence only. Rankings and bare numbered lists do not automatically become processes.
* Comparison: heading and complete pipe table, three columns (category, concept A, concept B), 1–4 category rows. Values remain authored strings; this does not imply superiority or causality.
* Dataset: heading and two-column pipe table, e.g. `Group | Height (cm)`, 2–6 uniquely labeled rows with explicit nonnegative dot-decimal values and one explicit common unit. No unit conversion, inferred units, mixed units, negatives, comma decimals, nonfinite values or arbitrary prose-to-chart conversion.
* Unordered lists and other headings are recognized as context, with typography/preserve fallback. Duplicate identities, omitted/extra rows, noncontiguous steps and ambiguous structures cannot be silently repaired. Extra prose prevents automatic structure extraction. Existing SVG limits still apply, including positive maximum and resolvable bar dimensions.

A Markdown table separator is accepted as syntax. The exact original text and all supporting line excerpts remain available, including table headers. An existing original visual does not suppress an alternative when explicit evidence supports one; Restore original remains the Phase 2B choice.

## Contracts and integrity

`grounded-design.v1` stores the exact base snapshot, extractor version, reconstructed evidence, page/revision-bound supporting excerpts, warnings, origin (`source_text` or `teacher_confirmed`) and optional proposal UUID. Excerpts have Unicode code-point start/end offsets and `display:start:end` locators. No arbitrary SVG/layout instructions are accepted.

There are deliberately two hashes:

* `source_hash` on candidates, requests, approvals and private selections remains the authoritative Phase 2B saved-source hash (`studio-source.v2`). Saving a proposal/review/selection never touches Project/page/draft timestamps and cannot invalidate itself.
* The plan/preview `source_hash` (also exposed as `design_source_hash`) hashes the derived evidence-enriched snapshot. It must independently reconstruct from the authoritative base plus deterministic extraction or a matching immutable teacher confirmation.

`verified_preview` validates this reconstruction, all identity/hash/version links, original Phase 1 semantic checks, SVG checks, and byte-identical deterministic rerendering before private delivery/selection. Current selection additionally compares the exact saved snapshot and source mode. Source text/narration/assets/scene/structure edits, splits, merges, deletions and restores invalidate the old source identity using Phase 2B rules. The adapter conservatively invalidates on saved draft/page revision stamps, even for some unrelated edits.

The strict evidence contract now permits an optional authored `heading` separate from the full display text. Grounding derives it from the first exact source line; required diagram labels still come from the entire supported structure. No source text is overwritten. Renderer `svg-educational.v2` correctly escapes newlines/tabs in XML attributes so multiline typography passes independent exact-label validation. Old renderer-version previews are incompatible; regenerate them. No legacy test-record conversion is implemented. The Phase 1 preview manifest was regenerated for the new renderer version; its SVG bytes remain unchanged.

## Optional local model proposals

`visual_evidence.py` reuses `OllamaLessonIntelligenceProvider.generate_structured`, a small public JSON entry point on the **existing** provider transport/configuration. It reuses the existing local model/base URL, deterministic temperature, synchronous timeout cap and provider exception conventions. There is no second HTTP client, model dependency, repair loop, progressive scheduler, video dependency or new worker queue.

The path is opt-in and gated by existing Intelligence/local-Ollama/provider-chain settings. Each selected-page request uses a maximum 8-second socket timeout (also bounded by the existing sync provider cap), one call, and at most 65,536 response bytes. It does not wait for lesson-wide inference. The timeout is a socket timeout, not an absolute end-to-end scheduling guarantee. Throttling bounds owner API request rate. Future expensive generation belongs in the shared Intelligence orchestration with a dedicated low-priority visual queue, independent of TTS/render/avatar resources. Production concurrency/resource arbitration with Ollama/XTTS remains future work; this phase adds no render queue.

`visual-proposal.v1` accepts only strategy, rationale, typed evidence data and exact source-span references. Unknown fields, duplicate JSON keys, malformed/oversized output, markup, unsupported operations, invented/missing steps and incorrect quotations fail closed. Source is delimited as untrusted data in the prompt; output validation, not the prompt, enforces safety.

Model support is intentionally narrow:

* Numeric/comparison proposals must exactly match independently extracted authoritative records. The model cannot override a number or supply a missing unit, even with confirmation.
* A model may arrange **every** item in a 2–4 item explicit unordered bullet list into a proposed sequence. Labels must match exactly; inferred order remains a teacher judgment. It cannot invent a step or causal relationship. Arbitrary free-prose conceptual/causal inference is unsupported.
* All model proposals require explicit review, including automatically source-backed ones. The server adds warnings; a model cannot mark itself verified.

Unavailable/timeout/invalid output creates no evidence approval. The panel calls the existing deterministic generation endpoint as fallback. Normal deterministic Generate never invokes Ollama.

## Private review and APIs

Migration `0040_grounded_visual_evidence` adds immutable `VisualEvidenceProposal`, immutable one-to-one `VisualEvidenceReview`, and candidate grounding/proposal references. Proposals deduplicate by owner/project/source/proposal hash. A review is a terminal confirm/reject decision: identical retries reuse it; an opposite decision returns 409. A rejected identical deterministic proposal is shown as rejected rather than randomized or resurrected. Different compatible proposals remain reviewable.

Owner-only, authenticated, private/no-store JSON endpoints:

* `GET /projects/{id}/visual-proposals/?page_key=…` — latest 20 private proposals/status/quotes.
* `POST /projects/{id}/visual-proposals/` — only `page_key`, `expected_source_hash`; optional bounded local call.
* `POST /projects/{id}/visual-proposals/{uuid}/decision/` — only `action` (`confirm`/`reject`), `expected_source_hash`.

Confirmation locks owner Project, saved pages and proposal, rechecks source/proposal integrity, records the immutable decision and generates/reuses a bounded safe candidate in one transaction. Opposing concurrent decisions serialize; real conflicts return 409. Confirmation does not select or save a private visual choice. Phase 2B's existing selection/version/history API remains the only choice persistence path. Candidate and approval scope is rechecked on the server; unknown writable fields and cross-owner/project identifiers are rejected.

Historical previews are independently verified against their stored immutable base and confirmation, with stale metadata; stale candidates cannot be selected. SVG delivery still uses authenticated image blobs, restrictive CSP/sandbox, no public media path and no DOM markup injection.

## Studio interaction

The existing Visuals ✨ tab now includes a standalone `VisualEvidencePanel`: source excerpts with locators, source-backed/teacher-confirmed status, optional local-model proposal generation, proposed typed structure, rationale, warnings, confirm/reject and stale status. Controls respect unsaved transcript edits. EN/TR/AR copy follows the existing localization module. Model/source text is rendered as React text, not HTML.

Confirm → preview does not invoke Use this design. The teacher explicitly stages and saves using the unchanged Phase 2B private-draft controls. Reopen recovers the saved choice, and Restore original remains available. Evidence requests are scoped to project/page/source hash, late responses after switches are discarded, repeated clicks are locked, and existing authenticated preview object-URL cleanup is reused. Analysis remains mounted and unchanged. Working completed-scene previews remain inside Visuals, with no duplicate transcript overlay.

## Rendering and publication isolation

No changes to `core.drafts`, general views/save/promotion logic, TranscriptPage fields, scene modes, render planner/manifests, workers, TTS, subtitles, video assets, moderation or publication. Review/selection store separate private rows; no Project/page/job mutation, render dirtiness, task dispatch or global Save/Save & Rerender coupling. No selected SVG is passed to a worker. Database regressions compare protected Project/TranscriptPage/Job state before and after the entire flow.

Phase 3 still requires immutable draft revision capture and promotion of exactly the rendered revision, explicit completed-scene semantics, independent audio reuse validation, targeted-render fingerprints, safe selected-asset storage and rollback/version policy. Phase 2C is not authorization to implement these.

## Reproducible previews

`python docs/visual_design/phase2c/generate_previews.py` needs only the standard library and pure modules. Eight examples, exact source/plan/preview hashes and provenance are in `docs/visual_design/phase2c/manifest.json`; open `index.html` for all examples or `contact_sheet.svg` for process/comparison/chart/confirmed-list overview. Fixtures: `tests/fixtures/visual_design/grounded/examples.json`. Synthetic confirmation is explicitly labeled and does not create database approvals. No models or dependencies are downloaded by the generator.

Rasterized process/comparison/chart/confirmed-list previews were visually inspected using temporary CairoSVG review tooling; no project dependency or binary artifact was added.

## Verification

Local verification so far:

* Focused backend/extraction/Intelligence/draft regression run: **268 passed, 4 skipped, 63 subtests passed**, 51.03 seconds. Skips include PostgreSQL-only row-lock assertion and existing optional-runtime tests. Final evidence/artifact/cleanup run: **52 passed, 1 PostgreSQL-only skip**, 31.68 seconds. Two existing Django deprecation warnings remain.
* Frontend: `npm ci` succeeded; `npm run test:ci -- --maxWorkers=2` **49 files, 350 tests passed**. Build succeeded (existing >500 kB HLS chunk warning).
* Both `npm audit --audit-level=high` and `npm audit --omit=dev --audit-level=moderate`: **0 vulnerabilities**. No dependency files changed.
* Django check: no issues; migration drift check: no changes detected.
* `npm run e2e` was executed locally: all 36 launches were blocked by the missing Chromium executable. These are **not passing browser tests**. Hosted Chromium acceptance and PostgreSQL concurrency results will be recorded after CI completes.
 Baseline tests mock all model responses and require no Ollama/GPU. New coverage includes grounded structure fidelity, exact quotes, missing units, contradictory duplicate values, Turkish/English text, original visuals, hallucination/timeout/disabled fallback, private ownership, confirmation/rejection/idempotency, source staleness, tamper rejection, immutable review rows, cleanup, pipeline isolation, async page-switch handling and mobile/desktop browser review-before-selection.

## Known limitations

The explicit text grammar is intentionally small. Source formatting lost during extraction is not reconstructed. There is no OCR, semantic PPTX/PDF table extraction, free-form image analysis, general scientific correctness proof, chart-unit inference or unrestricted model reasoning. Existing renderer text/geometry limits fail explicitly rather than truncating; font-dependent pixels still require DejaVu Sans. SVG text support remains the existing English/Turkish Latin repertoire; Arabic UI is localized, but Arabic source shaping/rendering is unsupported. The local model requires actual installed/configured Ollama; baseline mocks establish behavior, not model quality or hardware performance. All suggestions and approvals remain private and preview-only.
