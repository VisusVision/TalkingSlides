# TalkingSlides Setup Assistant {{VERSION}}

TalkingSlides Setup Assistant is the native desktop and CLI onboarding, diagnostics, configuration, and local service-control companion for TalkingSlides.

## Downloads

- Windows GUI: `TalkingSlides-Setup-{{VERSION}}-windows-x64.exe`
- Windows CLI: `talkingslides-setup-cli-{{VERSION}}-windows-x64.exe`
- Linux portable package: `TalkingSlides-Setup-{{VERSION}}-linux-x64.tar.gz`
- Checksums: `SHA256SUMS.txt`

Windows and Linux are separate target-native packages.

## What is included in 0.2

- First-run detected-repository, existing-folder, safe-clone, and system-only choices.
- Multi-marker repository validation, persisted active repository, and bounded recent paths.
- Confirmed, cancellable, argv-only cloning from the public TalkingSlides repository.
- Accessible centralized service states with icon, text, description, and light/dark color roles.
- Declarative Services Control Center with background refresh, filters, individual controls, and runtime groups.
- Repository-scoped Docker Compose commands with no implicit build or pull.
- Bounded sanitized logs with copy, save, clear-display, and stoppable follow.
- Host-side Ollama detection, model inventory, safe assistant-owned start/stop, and external-manager guidance.
- Secret-blind configuration status and a classified Action Required panel.
- Development version `0.2.0-dev`; a future matching release tag overrides it at package time.

## Safety

- No automatic Docker, Git, Ollama, driver, operating-system service, or model installation.
- No implicit Docker build, pull, prune, image deletion, volume deletion, or `down -v`.
- Repository actions require a fully validated TalkingSlides checkout.
- External commands use argument arrays, native exit codes, separate stdout/stderr, timeouts, cancellation where appropriate, and sanitized descriptions.
- Secret values are never displayed.
- An Ollama process not started by the current assistant is never terminated.
- Existing `.env`, repositories, directories, databases, volumes, media, models, and user data are not overwritten or removed.

## Prerequisites and limits

Docker and Compose remain prerequisites for local runtime services. Git is required only for Clone. GPU and avatar requirements apply only to avatar-capable groups.

Ollama remains host-side. Version 0.2 reports configured/missing models and provides explicit manual pull guidance; it does not download models automatically.

Linux packages are portable executables, not distribution package-manager installers. Ubuntu CI remains the authoritative Linux GUI/package validation environment and uses `QT_QPA_PLATFORM=offscreen`.

## Verify checksums

PowerShell:

```powershell
Get-FileHash .\TalkingSlides-Setup-{{VERSION}}-windows-x64.exe -Algorithm SHA256
```

Linux:

```bash
sha256sum -c SHA256SUMS.txt
```
