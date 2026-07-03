# TalkingSlides Setup Assistant {{VERSION}}

TalkingSlides Setup Assistant is the native desktop and CLI setup, diagnostics, configuration, and local-runtime companion for TalkingSlides.

## Downloads

- Windows GUI: `TalkingSlides-Setup-{{VERSION}}-windows-x64.exe`
- Windows CLI: `talkingslides-setup-cli-{{VERSION}}-windows-x64.exe`
- Linux portable package: `TalkingSlides-Setup-{{VERSION}}-linux-x64.tar.gz`
- Checksums: `SHA256SUMS.txt`

Windows and Linux are separate native packages. The Windows `.exe` files do not run natively on Linux.

## What is included

- Quick Check for host, repository, configuration, Docker, Compose, and profile readiness.
- Full Check for deeper ports, Compose-file, model/cache, endpoint, and platform probes.
- Runtime profile previews and confirmed actions for `core`, `tts`, and `avatar`.
- Sanitized report export in JSON, Markdown, and text.
- Safe actions for creating a missing local `.env` from `.env.example`, creating `storage_local`, and repairing a selected Linux executable bit.

## Prerequisites and limits

Docker remains a prerequisite for local TalkingSlides runtime profiles. GPU and avatar requirements remain profile-specific, and avatar runtime actions require explicit acknowledgement before the avatar worker can be started.

The assistant does not automatically install Docker, GPU drivers, BIOS or firmware virtualization, large AI models, or other heavyweight prerequisites. It diagnoses and reports what is missing so users can make the required system changes deliberately.

Known limitations:

- The Linux GUI requires a compatible desktop display stack and C library.
- Linux packages are target-native portable executables, not distro package-manager installers.
- The assistant validates configured model markers but does not download models or run avatar renders.
- Runtime status can report services from another checkout that already owns expected ports; review the selected repository before mutating runtime state.

## Verify checksums

PowerShell:

```powershell
Get-FileHash .\TalkingSlides-Setup-{{VERSION}}-windows-x64.exe -Algorithm SHA256
```

Linux:

```bash
sha256sum -c SHA256SUMS.txt
```
