# Changelog

All notable release-managed changes for TalkingSlides / VisusVision are documented here.

This project follows semantic versioning as documented in [docs/RELEASE_PROCESS.md](docs/RELEASE_PROCESS.md). The release tag is the source of truth for a shipped version.

## [1.0.0] - 2026-06-04

### v1.0.0 Foundation

Initial release-management foundation for the current product baseline.

- Storage production readiness checks and documentation.
- Storage retention and orphan/capacity reporting.
- Render recovery reconciliation for stale jobs and follow-up intents.
- Manual render recovery inspection, resolve, and ignore operations with audit records.
- System observability report for operational snapshots.
- Prometheus metrics for render, recovery, availability, cached storage, and snapshot freshness.
- Grafana dashboard and initial alert rules for render operations and storage snapshot health.
- Cached storage metrics snapshot workflow to avoid expensive scrape-time storage scans.
- Snapshot freshness and stale-snapshot alert coverage.
- Suspended Kubernetes staging CronJob workflow for operator-owned storage metrics snapshots.
- Frontend audit fixes and validated frontend test/build flow.
- Docker build cache hardening for CI smoke builds.
- FastAPI TTS lifespan migration.
- Frontend route-level code splitting.

### Release Notes

- This changelog entry captures the foundation baseline already present on `developer` at the time the release branch was created.
- No application behavior, backend logic, frontend logic, dependency, Docker, or migration changes are part of this release documentation branch.
