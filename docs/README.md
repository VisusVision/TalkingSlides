# Documentation Index

## User Setup Docs

- [Windows installation](INSTALL_WINDOWS.md)
- [Local development quickstart](LOCAL_DEVELOPMENT_QUICKSTART.md)
- [Troubleshooting](TROUBLESHOOTING.md)

Useful Windows diagnostics:

- `scripts/windows-preflight.ps1` checks host prerequisites and profile readiness without installing or starting services.
- `scripts/windows-runtime-health.ps1` checks already-running services and HTTP endpoints without rebuilding or pulling images.

## Developer Docs

- [Local development](LOCAL_DEVELOPMENT.md)
- [Environment variables](ENVIRONMENT_VARIABLES.md)
- [Deployment profiles](DEPLOYMENT_PROFILES.md)
- [Architecture](ARCHITECTURE.md)
- [Repository health check](REPO_HEALTH_CHECK.md)
- [Release process](RELEASE_PROCESS.md)
- [Release checklist](RELEASE_CHECKLIST.md)
- [Changelog](../CHANGELOG.md)

## Runtime and AI Docs

- [Full stack local runtime](FULL_STACK_LOCAL_RUNTIME.md)
- [TTS coworker integration plan](TTS_COWORKER_INTEGRATION_PLAN.md)
- [TTS deterministic resolver plan](TTS_D1_DETERMINISTIC_RESOLVER_PLAN.md)
- [TTS LLM pronunciation suggestions plan](TTS_L1_LLM_PRONUNCIATION_SUGGESTIONS_PLAN.md)
- [Intelligence providers](INTELLIGENCE_PROVIDERS.md)
- [Avatar pipeline](AVATAR_PIPELINE.md)
- [Avatar model provisioning](AVATAR_MODEL_PROVISIONING.md)
- [Subtitle translation provider options](SUBTITLE_TRANSLATION_PROVIDER_OPTIONS.md)

## Roadmaps

- [Installer roadmap](INSTALLER_ROADMAP.md)
- [Partial rendering roadmap](PARTIAL_RENDERING_ROADMAP.md)
- [Avatar slide defaults roadmap](AVATAR_SLIDE_DEFAULTS_ROADMAP.md)
- [I18N roadmap](I18N_ROADMAP.md)
- [Avatar production roadmap](avatar-production-roadmap.md)
- [Roadmap and TODO](ROADMAP_TODO.md)
- [Render follow-up intent RFC](RENDER_FOLLOWUP_INTENT_RFC.md)
- [Studio page controls plan](STUDIO_PHASE_5C_PAGE_CONTROLS_PLAN.md)
- [Studio rerender sequence plan](STUDIO_PHASE_5C3_RERENDER_SEQUENCE_PLAN.md)

## Operations and Moderation

- [Operations runbook](OPERATIONS_RUNBOOK.md)
- [Production deployment](PRODUCTION_DEPLOYMENT.md)
- [Storage production readiness](STORAGE_PRODUCTION_READINESS.md)
- [Moderation operations](MODERATION_OPERATIONS.md)
- [Moderation smoke testing](MODERATION_SMOKE_TESTING.md)
- [Prometheus and retry runbook](PROMETHEUS_AND_RETRY_RUNBOOK.md)
- [Secure playback and DRM](SECURE_PLAYBACK_DRM.md)
- [Secure playback manual test checklist](secure-playback-manual-test-checklist.md)
- [CSP Report-Only foundation](CSP_REPORT_ONLY.md)

## Internal Audits and Plans

These documents are preserved for developer-branch context. Treat them as detailed planning or audit history unless a current guide links to a specific section.

- [Unfinished work](UNFINISHED_WORK.md)
- [Tradeoffs, plus and minus](TRADEOFFS_PLUS_MINUS.md)
- [Storage cleanup plan](ENV_STORAGE_CLEANUP_PLAN.md)
- [Repository cleanup audit](REPO_CLEANUP_AUDIT.md)
- [Rerender worker diagnosis](RERENDER_WORKER_DIAGNOSIS.md)
- [Runtime storage and lesson visibility diagnosis](RUNTIME_STORAGE_AND_LESSON_VISIBILITY_DIAGNOSIS.md)
- [Subtitle request hardening plan](SUBTITLE_REQUEST_HARDENING_PLAN.md)
- [Subtitle translation phase 3 plan](SUBTITLE_TRANSLATION_PHASE_3_PLAN.md)
- [Subtitle translation phase 4 generation](SUBTITLE_TRANSLATION_PHASE_4_GENERATION.md)
- [Highlight system plan](HIGHLIGHT_SYSTEM_PLAN.md)
