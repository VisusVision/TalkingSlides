# Tradeoffs, Plus and Minus

Developer-only. This file records design tradeoffs for the developer branch. Main should receive only stable decisions, not open debate notes.

## Avatar Overlay

| Choice | Plus | Minus |
| --- | --- | --- |
| Non-blocking avatar overlay | Base lesson publishes quickly; avatar failure does not block learning; GPU work can scale separately | Requires status UI and overlay synchronization; avatar may appear later than base video |
| Burned-in avatar video | One final video asset is simple for players | Rerender is expensive; avatar failure blocks or delays final output; less flexible for hide/move controls |
| Persona bank first | Safer moderation/legal posture; cacheable assets; predictable quality | Less personalized; requires persona asset pipeline |
| User face cloning | More personal and marketable | Higher consent, moderation, security, and GPU cost burden |

## MuseTalk-only vs LivePortrait + MuseTalk + Restoration

| Choice | Plus | Minus |
| --- | --- | --- |
| MuseTalk-only | Faster, simpler, lower GPU cost | Can look lip-only and less natural |
| LivePortrait + MuseTalk | Better motion and presentation quality | More runtime dependencies and tuning |
| Optional restoration | Better final quality when it works | More GPU cost and another failure mode |

Current direction: LivePortrait with vetted d11 template plus MuseTalk, restoration optional.

## Secure Stream vs DRM

| Choice | Plus | Minus |
| --- | --- | --- |
| `secure_stream` | Works without DRM vendor; supports tokenized HLS/MP4, watermark, heartbeat, and session lock | Not equivalent to studio-grade DRM |
| `drm_protected` | Stronger commercial content protection with provider support | Requires vendor, license server, packaging, EME player work, and operations |

Current direction: use `secure_stream` as production baseline; treat DRM as a later vendor-backed integration.

## Local Storage vs Object Storage

| Choice | Plus | Minus |
| --- | --- | --- |
| Local/shared filesystem | Simple and works with current code | Harder to scale horizontally; needs careful backup/retention |
| MinIO/S3 adapter | Better distributed worker story and cloud operations | Requires real storage abstraction work and migration planning |

Current direction: document durable filesystem requirements first; add object storage adapter when scaling demands it.

## Free/Local Dev vs Production Infrastructure

| Choice | Plus | Minus |
| --- | --- | --- |
| Local Docker/SQLite/fallbacks | Easy onboarding and lower cost | Can hide production-only failures |
| Managed Postgres/Redis/storage/GPU | More reliable and scalable | More operational cost and setup complexity |

Current direction: keep local dev permissive, but fail fast when `DEBUG=False`.

## Deferred Work

Some items are intentionally deferred because they need product, infrastructure, or vendor decisions:

- DRM vendor integration.
- Object storage adapter.
- Persona bank.
- Auto avatar placement.
- Full production monitoring and alerting.
- Full browser E2E coverage.
