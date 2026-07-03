# TalkingSlides Setup Assistant

TalkingSlides Setup Assistant is the cross-platform setup, diagnostics, configuration, and local-runtime application for TalkingSlides. Windows and Linux use the same Python core with thin platform adapters.

## Architecture and Framework Decision

The repository already uses Python for backend tooling and tests but had no desktop framework, Electron/Tauri application, or existing installer packaging stack. PySide6 was selected because Qt provides native Windows/Linux widgets, keyboard accessibility, system palette support, mature layout controls, and a windowed executable path without adding a Node desktop runtime. PyInstaller was selected for target-native standalone GUI and CLI bundles. The CLI imports neither PySide6 nor the GUI module during normal operation.

The shared code is organized under `tools/setup_assistant/`:

- `models.py`: stable result/action/profile models and aggregation;
- `runner.py`: argv-only subprocess execution, native exit codes, separate stdout/stderr, timeout, cancellation, encoding, and sanitization;
- `checks/`: shared check orchestration and profile filtering;
- `platforms/windows.py` and `platforms/linux.py`: isolated platform probes;
- `repository.py` and `resources.py`: repository discovery, saved preference, source/frozen/AppImage resource roots;
- `actions/` and `runtime.py`: narrow confirmed repairs and profile controls;
- `reports/`: JSON/Markdown/text rendering and recursive redaction;
- `gui.py` and `cli.py`: thin interfaces over the same core.

Tkinter was rejected because it would require more custom work to meet the requested modern status-card, theme, accessibility, and expandable-detail behavior. Electron was rejected because this repository has no Electron application and adding a second Node desktop runtime would duplicate packaging and process-management concerns.

## Supported Hosts

- Windows 10/11 64-bit; Windows 11 with Docker Desktop and WSL2 is the recommended Windows path.
- Current 64-bit Debian/Ubuntu, Fedora, and compatible desktop distributions with Docker Engine and the Compose plugin.
- `x86_64`/AMD64 and ARM64 are recognized. Release CI currently produces `x86_64` artifacts.
- The desktop UI requires a working Windows desktop, X11, or Wayland. The CLI works on headless hosts.

The application diagnoses Docker, WSL, drivers, permissions, and virtualization. It does not silently install or reconfigure those system components.

## Source Usage

The CLI core has no GUI dependency:

```text
python -m tools.setup_assistant --help
python -m tools.setup_assistant check --profile core
python -m tools.setup_assistant check --profile tts --full
python -m tools.setup_assistant check --profile avatar --full
python -m tools.setup_assistant report --format json
```

Install the desktop/build dependencies in a project virtual environment, then launch the GUI:

```text
python -m pip install -r requirements-setup-assistant.txt
python -m tools.setup_assistant gui
```

PySide6 is imported only by GUI mode. CLI checks and reports continue to work when PySide6 is not installed.

## Repository Selection

The Setup Assistant does not assume that its current working directory is the repository. It validates a selected folder using these markers:

- `infra/docker-compose.yml`
- `services/api`
- `services/frontend`
- `scripts`

Discovery supports source checkouts, frozen executables, portable applications beside a repository, AppImage-style runtime roots, an explicit `--repository` argument, and a saved user preference. The preference stores only the selected repository path under the user's application-config directory.

## Desktop Sections

1. **Welcome** shows the detected OS, application version, and Quick Check/Full Check actions.
2. **Requirements** summarizes OS, architecture, CPU, RAM, disk, Docker, Compose, WSL/virtualization, display, and profile-specific GPU state.
3. **Installation & Configuration** selects the repository and exposes narrow confirmed setup actions.
4. **System Diagnostics** shows grouped status rows, genuine step progress, expandable technical details, suggested fixes, recheck, and copyable commands.
5. **Runtime** previews exact profile commands and offers confirmed Start/Stop plus read-only Status/Health.
6. **Report** exports sanitized JSON, Markdown, or text and copies a readable summary.

The UI follows the system light/dark palette, keeps keyboard focus visible, and does not require a console window.

## Quick Check and Full Check

Quick Check covers the host, repository, configuration presence, Docker CLI/Compose/daemon, profile filtering, and git-state counts. It skips port, Compose-file, model-inventory, and service-endpoint probes.

Full Check adds:

- expected port occupancy;
- `docker compose config --quiet`;
- selected-profile model/cache checks;
- already-running API/TTS health endpoints;
- detailed platform checks.

Internet connectivity is skipped unless `--internet` is explicitly selected.

## Runtime Profiles

- `core`: PostgreSQL, Redis, MinIO, API, and optional frontend. No GPU/avatar requirement.
- `tts`: core plus TTS and worker services. No avatar requirement.
- `avatar`: TTS plus the avatar worker. GPU/runtime/model checks apply.

Examples:

```text
talkingslides-setup runtime status --profile core
talkingslides-setup runtime start --profile tts --no-frontend --confirm
talkingslides-setup runtime stop --profile avatar --confirm
```

On Windows, runtime actions delegate to `scripts/windows-runtime.ps1`. On Linux, the adapter uses argv-only Docker Compose commands with explicit services. Stop uses `docker compose stop`, never `down`, and preserves volumes and data.

Starting the avatar profile requires a second explicit queue-risk acknowledgement because an avatar worker can consume real queued work. Diagnostics never run a real avatar job.

## Safe Actions

Implemented actions are narrow and confirmation-gated:

- create `infra/.env` from `infra/.env.example` only when the target does not exist;
- create the empty `storage_local` directory;
- repair a selected local executable's user execute bit on Linux.

CLI actions preview by default and execute only with `--confirm`:

```text
talkingslides-setup action config.create_env --repository /path/to/TalkingSlides
talkingslides-setup action config.create_env --repository /path/to/TalkingSlides --confirm
```

The application can show commands or documentation for external prerequisites. It does not automatically install Docker, enable firmware virtualization, alter global firewall rules, change group membership, install GPU drivers, download large models, overwrite `.env`, delete containers/volumes, remove user data, or purge queues.

## Reports and Exit Codes

Reports contain check IDs, status/severity, summaries, technical details, remediation, timing, and sanitized diagnostic data. Secret-like assignments, authorization headers, URL credentials, and home-directory prefixes are redacted. Environment secret values and private media contents are never collected.

- `0`: diagnostics completed with no failures; warnings may be present.
- `1`: one or more diagnostic checks failed, or a runtime command returned nonzero.
- `2`: invalid usage, an invalid repository for a runtime action, or a required safety confirmation was omitted.

No ANSI color sequences are emitted by CLI output.

## Packaging

Repeatable specs live under `packaging/setup_assistant/`.

Windows CI builds:

- `TalkingSlides-Setup.exe` — windowed GUI, no normal console;
- `talkingslides-setup-cli.exe` — console CLI companion. The suffix is required because Windows filenames are case-insensitive.

Linux CI builds:

- `TalkingSlides-Setup` — portable GUI executable;
- `talkingslides-setup` — portable CLI executable;
- a versioned `.tar.gz` containing both.

The Linux tarball is the current equivalent portable Linux distribution. An AppImage can be added later without changing the shared core. A Windows `.exe` does not run natively on Linux. PyInstaller packages are built and smoked on their target operating system; they are never cross-labeled.

Run a local target-native build only from a project virtual environment:

```text
python packaging/setup_assistant/build.py
```

Generated `build/` and `dist/` content is ignored and must not be committed. `.github/workflows/setup-assistant-package.yml` runs focused tests before packaging and uploads versioned artifacts; it does not publish a release.

## Legacy Compatibility

`scripts/windows-doctor.ps1`, `VISUS-VidLab.bat`, and `scripts/visus-launcher.ps1` remain as deprecated compatibility entry points. They print a deprecation message, map supported legacy profiles, and redirect to TalkingSlides Setup Assistant while preserving the child exit code.

The older `windows-preflight.ps1`, `windows-runtime.ps1`, and `windows-runtime-health.ps1` remain available for scripted compatibility.

## Known Limitations

- The portable Linux GUI requires compatible system display and C-library support.
- Local Linux package validation requires an existing Linux/WSL environment with the project packaging dependencies; otherwise CI is the authoritative Linux build.
- The Setup Assistant validates configured model markers but does not download models or run an avatar render.
- Runtime status can report services from another checkout that already owns the expected ports; review the repository/project label before mutating runtime state.
