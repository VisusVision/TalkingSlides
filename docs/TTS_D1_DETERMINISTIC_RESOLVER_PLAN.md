# Phase TTS-D1: Deterministic Turkish/English Resolver Plan

## Summary

Implement backend-only deterministic Turkish/English acronym and technical-term resolution. Use a lightweight in-memory resolver that runs before synthesis, after teacher/manual overrides, without touching XTTS recovery, Studio transcript controls, worker queues, or adding LLM/network calls.

Current branch: `feat/tts-d1-deterministic-resolver`.

D1A status: implemented in the backend. D1B Studio preview display for unknown/ambiguous terms is implemented. Follow-up QA verified Turkish `ASP -> ey es pi`, case-insensitive `Pipeline/pipeline -> payp layn`, and Django preview fail-open local normalization. Llama/Ollama suggestions remain optional L1 and are not part of the render path.

## Precedence And Interfaces

Current precedence:

1. Generation path: saved project overrides are applied with placeholder protection in `tts_client`, then `prepare_text_for_tts`, then chunks are sent to `/synthesize` as `already_prepared=true`.
2. Preview path: request override maps are applied with placeholder protection, then `prepare_text_for_tts`, then placeholders are restored.
3. Normalizer path: structure cleanup, language glossary, Turkish/English normalizer, chunking, then XTTS -> gTTS -> silent fallback.

Target precedence:

1. Request-local/manual overrides.
2. Project glossary overrides, reserved for future shared project glossary. D1A uses existing project override maps only.
3. Global glossary.
4. Protected spans are honored by every resolver transform.
5. Acronym resolver.
6. Turkish known-word check.
7. English technical dictionary fallback.
8. Unknown/ambiguous term metadata and warnings.
9. No automatic LLM in render path.

D1A interface changes:

- Extend `TTSPreparedText` with defaulted `unknown_terms: list[str]` and `ambiguous_terms: list[str]`.
- Add the same fields to normalization preview responses and TTS synth metadata.
- Add optional `unknown_terms` and `ambiguous_terms` to synth request echo metadata for `already_prepared=true`.
- Add resolver rule entries to `tts_normalization_rules_applied` with `rule: "acronym"` or `rule: "english_technical_fallback"`.

D1B Studio interface:

- The Studio TTS preview reads `unknown_terms`, `ambiguous_terms`, and `tts_normalization_rules_applied` from the preview response.
- Unknown/ambiguous terms are shown as non-blocking helper warnings, not render errors.
- Teachers can add a detected term to local project override rows. Uppercase acronym-like terms default to `abbreviation`; other terms default to `mixed_word`.
- Added rows are draft-only until the teacher saves. Preview does not mutate transcript pages, captions, or rerender jobs.

## Key Implementation Design

Add a small resolver module under `services/tts_service/tts_preprocess/`, backed by package-local data files:

- `acronym_pronunciations.json`
- `tr_known_words.txt`
- `en_technical_terms.json`

Loading strategy:

- Use `functools.lru_cache(maxsize=1)` or module-level immutable caches.
- Normalize keys once with Unicode NFKC plus `casefold` for words; uppercase ASCII keys for acronyms.
- Tokenize each unprotected text segment once.
- Use O(1) dict/set lookup per token.
- Do not scan full dictionaries per token.
- Cap `unknown_terms` and `ambiguous_terms` to 20 unique first-seen surface forms.

Acronym ownership moved from `glossary.json` to `deterministic_resolver.py` plus `acronym_pronunciations.json`. Manual/project overrides still win first, the global glossary still handles product and phrase terms, and the deterministic resolver owns acronym expansion after glossary processing.

Turkish acronym outputs for D1A seed data:

| Term | Turkish spoken |
|---|---|
| AI | `ey ay` |
| API | `ey pi ay` |
| ASP | `ey es pi` |
| GPU | `ci pi yu` |
| HTML | `eyç ti em el` |
| CSS | `si es es` |
| SQL | `es ku el` |
| XML | `eks em el` |
| JSON | `cey son` |
| XTTS | `iks ti ti es` |
| TTS | `ti ti es` |
| DRM | `di ar em` |
| HLS | `eyç el es` |

Resolver behavior:

- In English mode, apply acronym pronunciations and leave normal words alone.
- In Turkish mode, apply acronym pronunciations first, leave Turkish-known words unchanged, apply English technical fallback only for curated known technical terms, and warn on suspicious unmatched ASCII technical-looking terms.
- Curated English technical fallback is case-insensitive; `Pipeline` and `pipeline` use the same `payp layn` spoken form in Turkish preview.
- Ambiguous terms are left unchanged and reported when they appear in both Turkish-known and English-technical maps.
- Captions/subtitles keep original text; only spoken TTS text changes.

What not to do:

- No Llama/Ollama/LLM in render path.
- No network calls per word.
- No heavy DB queries per token.
- No huge dictionaries.
- No replacement of teacher overrides.
- No change to XTTS -> gTTS -> silent fallback order.

## Test Plan

D1A focused tests:

- Manual/project override wins over acronym, glossary, and dictionary resolver.
- Acronym resolver works in a Turkish sentence for `GPU`, `HTML`, `CSS`, `SQL`, `XML`, and existing terms remain stable.
- Turkish known words are left unchanged and not warned.
- Curated English technical terms in Turkish text use deterministic fallback pronunciation.
- Turkish preview with `ASP ve Pipeline` emits `acronym` and `english_technical_fallback` rules.
- Unknown suspicious terms emit `unknown_terms` and warning metadata without blocking render.
- Ambiguous terms emit `ambiguous_terms` and remain unchanged.
- Preview and synth use the same resolver output and metadata.
- Captions/SRT stay based on original text, not spoken text.
- Tests assert no LLM/network hook is called in resolver path.

Run after D1A implementation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_tts_text_normalization.py tests/integration/test_tts_preview.py tests/integration/test_tts_preview_protection.py tests/integration/test_tts_service_text_quality.py -q
.\.venv\Scripts\python.exe -m py_compile services/tts_service/main.py services/scripts/tts_client.py services/tts_service/tts_preprocess/*.py
```

## Phases And D1A Prompt

Implementation phases:

- D1A: backend resolver, package data files, metadata plumbing, tests, docs.
- D1B: implemented. Studio preview displays unknown/ambiguous terms, resolver rules, and draft add-override actions.
- D1C: small curated dictionary expansion only from observed project needs.
- L1: optional Llama/Ollama suggestions for unknown terms only, never automatic render-path dependency.

Docs-only planning execution:

- Create `docs/TTS_D1_DETERMINISTIC_RESOLVER_PLAN.md`.
- Update `docs/TTS_COWORKER_INTEGRATION_PLAN.md` and `docs/UNFINISHED_WORK.md` with D1 status.
- Commit with `git commit -m "Plan deterministic TTS resolver"`.

Compact D1A implementation prompt:

```text
Implement Phase TTS-D1A backend only. Add a deterministic TTS resolver inside services/tts_service/tts_preprocess that runs after manual/project overrides and global glossary, before chunking/synthesis. Use small package-local acronym, Turkish-known-word, and English-technical maps loaded once and looked up O(1) per token. Do not add LLM/network calls, huge dictionaries, DB lookups per token, Studio UI, worker queue changes, or XTTS runtime changes. Preserve override priority and caption/original-text behavior. Return unknown_terms and ambiguous_terms in preview and synth metadata. Add tests for override precedence, Turkish acronym resolution, known Turkish words, English technical fallback, unknown warnings, preview/synth parity, captions remaining original, and no LLM render-path dependency.
```
