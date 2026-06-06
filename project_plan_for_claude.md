# Project: PPTX → Voice-Clone → Lesson Video (plan)

**Short purpose:** Build a web service where teachers upload a clean photo and a short voice sample to create a cloned voice profile, then upload lessons (.txt / .pptx / images / pdf / docx). The backend automatically produces high-quality 1080p MP4 lessons by synthesizing per-slide narration (Turkish/English detection), synchronizing with slide images, adding CC subtitles, and optional avatar animation for txt-only lessons. Prod storage: :contentReference[oaicite:0]{index=0}; local dev uses disk.

---

## 1 — High-level workflow
1. **Teacher Onboarding:** teacher uploads clean photo + 10–60s voice sample → creates `teacher_id`.  
2. **PPTX Parsing:** extract slides (HD images) and speaker notes (text). Use PowerPoint COM on Windows or `soffice`→PDF→`pdftoppm` on Linux. Output JSON list `{slide_index,image_path,note_text}`.  
3. **Voice Profile (two modes):**  
   - **Cloud:** instant clone API → `voice_id` (e.g., :contentReference[oaicite:1]{index=1}).  
   - **Local:** instantiate local clone model (Fish-Speech/Coqui) running on GPU; return `voice_id`/checkpoint.  
4. **TTS per-slide:** detect language (tr/en); synthesize `slide_###.mp3` using selected voice. Support breath/pause tuning per slide.  
5. **Timeline:** `slide_duration = audio_duration + pause_seconds` (pause param set by teacher).  
6. **Video parts:** ffmpeg produces per-slide MP4s from image+audio (`-shortest`, scale=1920x1080); optionally add fade transitions. (Video engine: :contentReference[oaicite:2]{index=2})  
7. **Concat → Final MP4:** concat parts, mux master audio if needed → `final_lesson.mp4`. Generate `.srt` from texts and timing.  
8. **Storage & Delivery:** local dev: `storage_local/output/...`. Prod: upload to Cloud Storage (:contentReference[oaicite:3]{index=3}) and serve via CDN (e.g., :contentReference[oaicite:4]{index=4}).  
9. **UI:** teacher dashboard (onboarding, uploads, project list, preview), student view (video + CC toggle + transcript).

---

## 2 — Core components (one line each)
- API / Admin: :contentReference[oaicite:5]{index=5} — user/project management & admin UI.  
- ML / Inference microservice: :contentReference[oaicite:6]{index=6} — TTS inference & voice-clone control.  
- Queue / Workers: :contentReference[oaicite:7]{index=7} + :contentReference[oaicite:8]{index=8}.  
- Local cloning models: :contentReference[oaicite:9]{index=9} or :contentReference[oaicite:10]{index=10} (PyTorch: :contentReference[oaicite:11]{index=11}).  
- Containerization: :contentReference[oaicite:12]{index=12}.  
- DB: :contentReference[oaicite:13]{index=13}.  
- Local object store (dev): MinIO.  
- Git & CI: :contentReference[oaicite:14]{index=14}.  
- (Optional Prod TTS provider): ElevenLabs (see above).  
- Object storage alternative: :contentReference[oaicite:15]{index=15} (if desired).

> Note: each organization/company above is mentioned once here and will be referenced generically elsewhere in docs.

---

## 3 — Requirements (dev vs prod)
- **Dev:** Windows or Linux, Python 3.10+, ffmpeg installed, docker & docker-compose, optional GPU for local cloning. Local storage under `storage_local/`.  
- **Prod:** GPU workers (NVIDIA 12GB+), managed storage, autoscaling worker pool.

---

## 4 — Folder layout (recommended)

project-root/
├─ infra/
│ ├─ docker-compose.yml
│ └─ dockerfiles/
├─ services/
│ ├─ api/ # Django project + REST admin
│ ├─ tts_service/ # FastAPI inference (cloud/local switch)
│ ├─ worker/ # Celery tasks: pptx parse, tts synth, ffmpeg render
│ └─ frontend/ # React teacher/student UI
├─ models/ # model checkpoints (gitignored)
├─ storage_local/ # dev storage: uploads + outputs
│ ├─ uploads/
│ └─ output/
│ ├─ images/
│ ├─ notes/
│ ├─ audio/
│ ├─ parts/
│ └─ final/
├─ scripts/
├─ tests/
├─ docs/
└─ README.md

---

## 5 — Subsystems to hand Claude next (priority)
A. Infra: `docker-compose.yml` + Dockerfiles (api, tts_service, worker, postgres, redis, minio).  
B. API: Django skeleton with models: Teacher, Project, Slide, VoiceProfile, Job; file upload endpoints.  
C. Worker: Celery skeleton with example tasks (pptx_extract, tts_synthesize, render_video).  
D. TTS service: FastAPI skeleton with endpoints for cloud-clone and local-clone control.  
E. Scripts: `scripts/pptx_extract.py`, `scripts/ffmpeg_helpers.py`.  
F. Tests: unit stubs & integration test for pptx->images + notes.  
G. Docs: mkdocs or markdown quickstart.

---

## 6 — Tests (quick)
- Unit: pptx parsing → images + notes; TTS synth → mp3.  
- Integration: sample pptx + voice → final mp4 (1080p) exists.  
- E2E: upload → job complete → URL reachable.  
- Language detection: tr/en samples verify correct TTS voice selection.  
- Subtitles: .srt timings aligned to slide durations.

---

## 7 — Claude usage & chunking (how to avoid wasting quota)
- Provide Claude only one subsystem at a time. Request **file contents only**. Test locally and commit before asking for the next chunk.  
- First deliverable: **infra/docker-compose.yml** + base Dockerfiles. (Use the Adım A prompt provided separately.)

---
