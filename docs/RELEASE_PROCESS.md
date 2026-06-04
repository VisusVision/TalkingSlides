# Release Process

This document defines how TalkingSlides / VisusVision versions are named, validated, approved, tagged, released, and rolled back.

Use [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) as the operational checklist for each staging or production release. Use [../CHANGELOG.md](../CHANGELOG.md) as the durable user-facing release history.

## Versioning Policy

The project uses semantic versioning: `MAJOR.MINOR.PATCH`.

- `MAJOR`: incompatible product, API, deployment, storage, or operator workflow changes.
- `MINOR`: backward-compatible features, operational capabilities, or significant UI/backend additions.
- `PATCH`: backward-compatible fixes, documentation corrections, CI-only fixes, and low-risk operational polish.

Pre-release versions may use suffixes such as `1.0.0-rc.1` when a release candidate needs staging validation before production.

## Version Source Of Truth

The Git tag is the source of truth for a shipped release.

- Release tags use `vMAJOR.MINOR.PATCH`, for example `v1.0.0`.
- Release-candidate tags use `vMAJOR.MINOR.PATCH-rc.N`, for example `v1.0.0-rc.1`.
- `CHANGELOG.md` must contain the matching version entry before tagging.
- `services/frontend/package.json` currently has its own package version and is not the product release source of truth.
- Backend service, model, prompt, and engine version constants are component metadata, not product release versions.

If a future product version needs to be exposed at runtime, add that in a separate reviewed change with tests and deployment notes.

## Branch And Tag Naming

- Release branches use `release/vMAJOR.MINOR[-name]`, for example `release/v1-foundation` or `release/v1.1`.
- Hotfix branches use `hotfix/vMAJOR.MINOR.PATCH[-name]`.
- Production tags use `vMAJOR.MINOR.PATCH`.
- Release-candidate tags use `vMAJOR.MINOR.PATCH-rc.N`.

Create release branches from the intended base branch only after confirming the base branch is clean and up to date with origin.

## Release Documentation Requirements

Every production release must include:

- A `CHANGELOG.md` entry with the version, date, highlights, validation notes, and known limitations.
- A completed release ticket or copied `docs/RELEASE_CHECKLIST.md`.
- The exact Git SHA, tag, image tags, and deployment target.
- Any migration, rollback, worker-drain, storage, or manual operator notes.
- Links to CI runs and staging validation evidence.

## CI Gates

Before a production tag or GitHub release is created, required checks must be green for the release branch or final release SHA:

- Django checks and migration dry run.
- Backend test suite or approved release-specific subset plus CI backend job.
- Frontend audit at high severity or stricter.
- Frontend tests.
- Frontend production build.
- Docker build smoke for API, worker, and TTS images.
- k6 load-test syntax smoke.

Any skipped or manually substituted gate must be recorded in the release ticket with owner approval.

## Staging Validation

Staging validation must run against production-like configuration without committed secrets.

Minimum staging validation:

- API readiness endpoint.
- Frontend load and login.
- Upload of a small source file.
- Render completion.
- Playback token and media playback.
- Catalog visibility for draft, published, and unpublished states.
- Storage smoke check against the mounted staging storage root.
- Observability review for render queue, failed jobs, storage snapshot freshness, and alert noise.

Feature-specific validation should be added when the release touches avatar, moderation, subtitles, secure playback, HLS, DRM, storage, or worker routing.

## Production Approval

Production release approval must confirm:

- Release owner and approver.
- Final SHA and tag.
- CI and staging evidence.
- Environment/profile validation.
- Backup status for database and media storage.
- Rollback path and previous artifact availability.
- Maintenance window or rollout plan.
- On-call or release owner availability through post-deploy monitoring.

Do not create a production GitHub release until approval is recorded.

## GitHub Tag And Release Flow

1. Freeze the release SHA.
2. Confirm `CHANGELOG.md` has the matching version.
3. Confirm CI gates and staging validation are complete.
4. Create the annotated tag:

```powershell
git tag -a v1.0.0 -m "Release v1.0.0"
```

5. Push the tag:

```powershell
git push origin v1.0.0
```

6. Create a GitHub Release from the tag.
7. Paste the changelog highlights and known limitations into the GitHub Release notes.
8. Attach or link CI, image, deployment, and staging evidence.

For release candidates, use the same flow with an `-rc.N` tag and mark the GitHub Release as a pre-release.

## Rollback Notes

Rollback planning must happen before deploy.

- Keep the previous app images and release SHA available until post-deploy smoke passes.
- API, worker, and TTS images should roll back as a compatible set when task payloads or media contracts changed.
- Database rollback must be handled through the deployment-specific database backup and migration policy.
- Media storage rollback must be handled independently from application rollback.
- If migrations shipped, document whether they are backward-compatible before deployment.
- If worker task signatures changed, drain workers or pause queues before changing image versions.

After rollback, record the rolled-back SHA/tag, reason, operator, time, and follow-up owner.
