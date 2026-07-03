# TalkingSlides Setup Assistant

TalkingSlides Setup Assistant is the cross-platform repository onboarding, diagnostics, configuration, and local service-control application for TalkingSlides. The released version is `setup-assistant-v0.1.0`; source builds on `developer` identify as `0.2.0-dev` until a future `setup-assistant-v0.2.0` release is created.

## Architecture

The existing Python and PySide6 application remains the single framework. Version 0.2 extends it rather than introducing a second launcher or service-management stack:

- `tools/setup_assistant/app.py`, `__main__.py`, and `cli.py` are the source entry points.
- `packaging/setup_assistant/gui_entry.py` and `cli_entry.py` are the frozen entry points.
- `gui.py` owns the Qt navigation and delegates work to background `QThread` workers.
- `models.py` contains diagnostics models; `status.py` is the single status-to-label, icon, description, and light/dark color mapping.
- `repository.py` owns multi-marker validation, discovery, external preferences, recents, and system-only state.
- `clone.py` owns confirmed, cancellable, argv-only Git cloning.
- `checks/engine.py` produces `CheckRun` and `CheckResult` diagnostics through the existing Windows/Linux adapters.
- `services.py` contains the declarative service and group registries, Compose discovery, scoped commands, operation conflicts, status parsing, and bounded logs.
- `ollama.py` is the host-side Ollama adapter.
- `configuration.py` emits secret-blind configuration states and Action Required items.
- `runner.py` executes argument arrays without a shell, preserves stdout/stderr and native exit codes, supports timeout/cancellation and progress callbacks, and sanitizes output.
- `actions/`, `runtime.py`, and `reports/` remain the narrow safe-action, runtime-wrapper, and sanitized-report layers.

The application stores user preferences in the operating system's user configuration directory, not in the selected Git checkout. It does not use repository-local settings or store credentials.

## Supported Hosts

- Windows 10/11 64-bit; Docker Desktop with WSL2 is the recommended Windows runtime.
- Current 64-bit Ubuntu/Debian, Fedora, and compatible desktop Linux systems with Docker Engine and Compose v2.
- The GUI requires a desktop session. The CLI works on headless systems.
- Release CI builds target-native Windows and Ubuntu artifacts. Windows executables are not Linux executables.

The assistant diagnoses prerequisites but never installs Docker, Git, Ollama, GPU drivers, operating-system services, or AI models.

## Release Downloads and Source Usage

The published v0.1.0 release files remain:

- `TalkingSlides-Setup-0.1.0-windows-x64.exe`
- `talkingslides-setup-cli-0.1.0-windows-x64.exe`
- `TalkingSlides-Setup-0.1.0-linux-x64.tar.gz`
- `SHA256SUMS.txt`

Source commands:

```text
python -m tools.setup_assistant gui
python -m tools.setup_assistant check --profile core --repository C:\path\to\TalkingSlides
python -m tools.setup_assistant check --profile core --system-only
python -m tools.setup_assistant report --profile tts --full --format json
```

Install GUI and packaging dependencies only in a project virtual environment:

```text
python -m pip install -r requirements-setup-assistant.txt
```

The CLI does not import PySide6 during normal CLI operation.

## First-Run Repository Onboarding

When no saved valid repository exists, the Repository page offers:

1. **Use detected repository** for a compatible checkout found near the executable, current directory, environment override, or recent paths.
2. **Choose existing folder** with a native folder picker.
3. **Clone TalkingSlides** from the centrally configured public URL.
4. **Continue with system checks only** without selecting a repository.

The page displays the candidate path, validation state, and every missing marker. A folder is valid only when all of these project markers have the expected file/directory type:

- `infra/docker-compose.yml`
- `scripts/windows-runtime.ps1`
- `tools/setup_assistant/__init__.py`
- `services/api`
- `services/frontend`

Folder name alone is never trusted. Git metadata is reported when present but is not mandatory for a source archive. Paths are canonicalized without shell interpolation and support spaces, Unicode, frozen executables, portable installations, and working directories outside the checkout.

The header always shows repository mode or system-only mode. It also provides a recent-repository selector and a route back to onboarding.

## Repository Persistence

The settings file contains only:

- schema version;
- most recently validated canonical repository path;
- at most five recent canonical paths;
- system-only preference.

Opening a valid saved repository is automatic. If it disappears or fails validation, onboarding returns. Selecting a repository moves it to the front of recents. Invalid/stale entries can be forgotten through the settings model. No repository URL credentials, tokens, environment values, or Git credentials are stored.

## Safe Clone Workflow

The default public URL is:

```text
https://github.com/VisusVision/TalkingSlides.git
```

The repository's public default branch is `main`, so `main` is the user-facing default ref. Developers can override the central defaults with `TALKINGSLIDES_CLONE_URL` and `TALKINGSLIDES_CLONE_REF`, or edit the advanced ref field.

Before cloning, the assistant:

- requires an installed Git executable and gives manual installation guidance if absent;
- rejects URLs containing embedded credentials;
- verifies that the destination parent exists and is writable;
- rejects a non-empty incompatible destination;
- activates an already-compatible checkout instead of overwriting it;
- shows the sanitized URL, canonical destination, selected ref, operation explanation, and argv preview;
- requires confirmation.

Git is launched without a shell:

```text
git clone --progress --branch <ref> --single-branch <url> <destination>
```

Stdout and stderr stay separate, progress is streamed into a bounded view, cancellation is supported, and the native exit code is retained. On failure or cancellation, only a new incomplete destination created by that attempt is removed. A pre-existing directory is never deleted. Successful output must pass the full repository validation before automatic activation.

## System-Only Diagnostics

System-only mode still checks the host, Docker command/Compose/daemon, expected ports, platform prerequisites, and host-side Ollama. Repository-dependent diagnostics return `Repository required` or a skipped repository result instead of crashing. All service starts, stops, builds, pulls, repository configuration actions, and repository reports remain gated.

CLI system-only use is explicit:

```text
talkingslides-setup check --system-only
talkingslides-setup report --system-only --format json
```

## Desktop Navigation

1. **Overview**: active mode, quick/full checks, high-level safety statement, and Action Required list.
2. **Repository**: detected/existing/clone/system-only onboarding.
3. **Requirements**: host and optional-integration readiness summary.
4. **System Diagnostics**: grouped results, progress, details, remediation, and safe commands.
5. **Services**: filtered service cards, refresh, automatic refresh, individual actions, and runtime groups.
6. **Configuration**: variable name/presence/format/requirement/feature only.
7. **Report**: sanitized JSON, Markdown, and text export.

Keyboard focus remains visible, controls have accessible names/tooltips, and every status uses icon plus text rather than color alone.

## Status States

The centralized status model is:

| State | Role | Meaning |
| --- | --- | --- |
| Checking | blue | A check or operation is busy |
| Healthy / Running | green | Healthy endpoint or running service |
| Starting / Stopping / Degraded | orange | Transitional or attention required |
| Stopped / Blocked / Failed | red | Unavailable, prerequisite blocked, or failed |
| Not configured / Optional / Unknown | gray | Configuration absent, optional, or indeterminate |

Each state also has a stable icon, text label, accessible description, and separate contrast-aware light/dark color.

## Services Control Center

The declarative registry describes stable ID, display name, category, service type, optionality, repository requirement, Compose service/profile, health URL, ports, configuration variables, supported actions, guidance, and documentation.

Discovered Compose services are:

- Core: `api`, `frontend`
- Data: `postgres`, `redis`, `minio`
- Media/TTS: `tts_service`, `worker`
- Optional integrations: `worker-avatar`, `libretranslate`
- Host application: Ollama
- External HTTP dependency: optional translation API configuration
- Configuration-only dependency: optional Google OAuth configuration

Compose profiles are discovered from `infra/docker-compose.yml`: `avatar` and `translation`. User-facing groups are exposed only when `scripts/windows-runtime.ps1` declares them and their services exist:

- Core
- Media / TTS
- Optional translation
- Avatar
- Full supported environment

Every group lists included services, prerequisites, resource impact, and start/stop actions. Windows group operations reuse `scripts/windows-runtime.ps1`. Optional avatar/full groups warn about GPU/resource use and queued avatar work.

Service refresh and operations run outside the GUI thread. A service cannot run conflicting operations concurrently, and repository selection is locked during an active repository/service operation.

## Docker Safeguards

Individual actions always use the selected checkout's exact Compose file and project directory. Start is equivalent to:

```text
docker compose -f <repo>/infra/docker-compose.yml --project-directory <repo> up -d --no-build --pull never <service>
```

Restart is an explicit `stop` followed by the same guarded `up`. The assistant never runs `down -v`, volume removal, prune, image deletion, implicit build, or implicit pull.

If the image is absent, Start reports `Image unavailable`. If a local image must be built, it reports `Build required`. Pull image and Build image are separate confirmation dialogs with network/resource warnings.

Every mutation shows the exact command and affected service(s), requires confirmation, and displays native exit codes plus sanitized stdout/stderr.

## Service Logs

Compose logs use timestamps, no color, and a bounded tail of at most 500 retained lines. The log dialog supports:

- Copy
- Save sanitized logs
- Clear display
- Start/stop live follow

Clear affects only the displayed text. It never deletes Docker logs. Live follow uses an argv-only `QProcess` and stops when requested or when the dialog closes.

## Host-Side Ollama

Ollama is not a Compose service in this repository. The assistant probes:

```text
http://localhost:11434/api/tags
```

It distinguishes optional/not needed, required by enabled local configuration, not installed, installed but stopped, running/healthy, no models, missing configured models, unhealthy endpoint, and port conflict.

The host adapter safely locates the executable, reports its sanitized version, and lists model names from the API. Required models come from actual `OLLAMA_*_MODEL` values in `infra/.env`. Starting launches `ollama serve` without blocking the GUI, captures bounded sanitized startup output, waits for health, and records process ownership.

Stop is available only for the exact process started by the current assistant process. An unrelated user-owned, systemd-managed, or Docker-managed Ollama process is never terminated. Detected external management receives manual guidance. Installation and model pulls are never automatic. Model-download status and exact `ollama pull <model>` guidance are provided; a future downloader may add separately confirmed progress/cancellation.

## Configuration and Privacy

The Configuration page and Action Required panel classify findings as blocking, required, recommended, optional, or informational. Each item identifies the reason, affected feature, exact next step, available safe action, and documentation target.

Environment inspection exposes only:

- variable name;
- present or missing;
- valid or invalid format;
- required or optional;
- affected feature.

Values never appear in models, widgets, reports, logs, or command previews. Secret-like output, authorization material, URL credentials, and assignments are redacted.

`infra/.env` can be created from `infra/.env.example` only after a preview and confirmation and only when the target does not exist. Existing files are never overwritten. The assistant does not invent credentials or treat placeholder secrets as valid.

## Diagnostics, Reports, and Exit Codes

Quick Check covers host, repository/system-only state, configuration presence, Docker, selected profile, Ollama, and Git counts. Full Check adds ports, Compose parsing, model/cache inventory, and already-running health endpoints. Internet probing remains opt-in.

- `0`: diagnostics completed without failures; warnings may exist.
- `1`: a diagnostic or external command failed.
- `2`: invalid usage, repository gating, or missing confirmation.

Reports remain recursively sanitized and do not collect private media.

## Packaging and Versioning

Run a target-native build:

```text
python packaging/setup_assistant/build.py
```

Development builds use `0.2.0-dev`. A matching `setup-assistant-vMAJOR.MINOR.PATCH` tag overrides the development version through the existing runtime hook. No v0.2.0 tag is created by development work.

Generated `build/`, `dist/`, executable, archive, report, environment, model, media, and runtime files must not be committed. Ubuntu package tests run with:

```text
QT_QPA_PLATFORM=offscreen
```

## Limitations

- Ollama model download execution is guidance-only in v0.2 development.
- Linux GUI/package validation requires a compatible Ubuntu/Linux runner; Windows cannot produce an authoritative Linux binary.
- Service health is based on Docker state and configured endpoints; an occupied port alone is not assumed to belong to the selected checkout.
- Pull and build are deliberately separate, potentially expensive actions.
- The assistant does not install or elevate system prerequisites.

## Legacy Compatibility

The existing `windows-preflight.ps1`, `windows-runtime.ps1`, `windows-runtime-health.ps1`, `windows-doctor.ps1`, `VISUS-VidLab.bat`, and `scripts/visus-launcher.ps1` remain available. Setup Assistant reuses their supported contracts and does not modify their destructive-data safeguards.
