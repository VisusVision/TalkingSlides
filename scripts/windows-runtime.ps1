[CmdletBinding()]
param(
    [string]$Profile = "",
    [ValidateSet("start", "status", "stop", "health", "preflight")]
    [string]$Action = "start",
    [switch]$PreflightOnly,
    [switch]$HealthOnly,
    [switch]$Status,
    [switch]$Stop,
    [switch]$NoHealth,
    [switch]$SkipPreflight,
    [switch]$NoFrontend,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ComposeFile = Join-Path $RepoRoot "infra\docker-compose.yml"
$PreflightScript = Join-Path $PSScriptRoot "windows-preflight.ps1"
$HealthScript = Join-Path $PSScriptRoot "windows-runtime-health.ps1"
$ValidProfiles = @("core", "worker", "tts", "avatar", "translation", "full")
$script:LastChildExitCode = 0

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Test-CommandAvailable {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Resolve-Action {
    $switchCount = 0
    foreach ($flag in @($PreflightOnly.IsPresent, $HealthOnly.IsPresent, $Status.IsPresent, $Stop.IsPresent)) {
        if ($flag) {
            $switchCount += 1
        }
    }
    if ($switchCount -gt 1) {
        Write-Error "Choose only one action switch: -PreflightOnly, -HealthOnly, -Status, or -Stop."
        exit 1
    }
    if ($PreflightOnly) { return "preflight" }
    if ($HealthOnly) { return "health" }
    if ($Status) { return "status" }
    if ($Stop) { return "stop" }
    return $Action.ToLowerInvariant()
}

function Resolve-Profile {
    param(
        [string]$RequestedProfile,
        [string]$EffectiveAction
    )

    if ([string]::IsNullOrWhiteSpace($RequestedProfile)) {
        if ($EffectiveAction -eq "stop") {
            return "full"
        }
        return "core"
    }

    $normalized = $RequestedProfile.ToLowerInvariant()
    if ($ValidProfiles -notcontains $normalized) {
        Write-Error "Unsupported profile '$RequestedProfile'. Choose one of: $($ValidProfiles -join ', ')."
        exit 1
    }
    return $normalized
}

function Get-ProfileServices {
    param(
        [string]$SelectedProfile,
        [bool]$ExcludeFrontend = $false
    )

    $core = @("postgres", "redis", "minio", "api")
    if (-not $ExcludeFrontend) {
        $core += "frontend"
    }
    switch ($SelectedProfile) {
        "core" { return $core }
        "worker" { return $core + @("worker") }
        "tts" { return $core + @("tts_service", "worker") }
        "avatar" { return $core + @("tts_service", "worker", "worker-avatar") }
        "translation" { return $core + @("libretranslate") }
        "full" { return $core + @("tts_service", "worker", "worker-avatar", "libretranslate") }
    }
}

function Test-UsesTranslationProfile {
    param([string[]]$Services)
    return $Services -contains "libretranslate"
}

function Test-UsesAvatarProfile {
    param([string[]]$Services)
    return $Services -contains "worker-avatar"
}

function Invoke-ChildScript {
    param(
        [string]$ScriptPath,
        [string]$SelectedProfile,
        [switch]$PassNoFrontend,
        [switch]$PassJson
    )

    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath)
    if (-not [string]::IsNullOrWhiteSpace($SelectedProfile)) {
        $args += @("-Profile", $SelectedProfile)
    }
    if ($PassNoFrontend) {
        $args += "-NoFrontend"
    }
    if ($PassJson) {
        $args += "-Json"
    }
    & powershell @args
    $script:LastChildExitCode = $LASTEXITCODE
}

function Invoke-Compose {
    param([string[]]$ComposeArgs)

    if (-not (Test-CommandAvailable "docker")) {
        Write-Error "Docker was not found. Install/start Docker Desktop first."
        return 1
    }

    & docker @ComposeArgs
    return $LASTEXITCODE
}

function Get-ComposeBaseArgs {
    param(
        [bool]$WithTranslationProfile,
        [bool]$WithAvatarProfile
    )

    $args = @("compose", "-f", $ComposeFile)
    if ($WithTranslationProfile) {
        $args += @("--profile", "translation")
    }
    if ($WithAvatarProfile) {
        $args += @("--profile", "avatar")
    }
    return $args
}

function Write-ProfileWarnings {
    param([string]$SelectedProfile)

    if ($SelectedProfile -in @("avatar", "full")) {
        Write-Warning "The avatar profile requires NVIDIA GPU and Docker GPU support."
        Write-Warning "The first avatar-capable image build can be heavy, but this wrapper does not build images."
        Write-Warning "Avatar heavy dependencies must be inside the Docker worker image, not the Windows virtual environment."
    }
    if ($SelectedProfile -eq "full") {
        Write-Warning "Ollama remains host-side in this phase. This wrapper does not install, start, or pull Ollama models."
        Write-Warning "If Ollama is unavailable, intelligence uses heuristic/fallback behavior according to current config."
    }
}

function Write-ServiceUrls {
    param([string[]]$Services)

    Write-Section "URLs"
    if ($Services -contains "frontend") {
        Write-Host "Frontend:          http://localhost:3000"
    }
    if ($Services -contains "api") {
        Write-Host "API:               http://localhost:8000"
        Write-Host "API readiness:     http://localhost:8000/api/v1/ready/"
    }
    if ($Services -contains "tts_service") {
        Write-Host "TTS:               http://localhost:8001"
    }
    if ($Services -contains "libretranslate") {
        Write-Host "LibreTranslate:    http://localhost:5000"
    }
    Write-Host "Ollama host-side:  http://localhost:11434"
    if ($Services -contains "minio") {
        Write-Host "MinIO console:     http://localhost:9001"
    }
}

Set-Location $RepoRoot

$effectiveAction = Resolve-Action
if ($effectiveAction -notin @("start", "status", "stop", "health", "preflight")) {
    Write-Error "Unsupported action '$effectiveAction'."
    exit 1
}

if ($Json -and (-not ($effectiveAction -in @("preflight", "health")))) {
    Write-Warning "JSON output is delegated only for preflight and health actions. Continuing with human-readable wrapper output."
}

if (-not ($Json -and ($effectiveAction -in @("preflight", "health")))) {
    Write-Host "VISUS VidLab Windows runtime wrapper"
    Write-Host "Repo root: $RepoRoot"
    Write-Host "Action: $effectiveAction"
}

switch ($effectiveAction) {
    "preflight" {
        $selectedProfile = Resolve-Profile -RequestedProfile $Profile -EffectiveAction $effectiveAction
        if (-not $Json) {
            Write-Host "Profile: $selectedProfile"
            if ($NoFrontend) {
                Write-Host "Frontend: excluded by -NoFrontend"
            }
            Write-Section "Preflight"
        }
        Invoke-ChildScript -ScriptPath $PreflightScript -SelectedProfile $selectedProfile -PassNoFrontend:$NoFrontend -PassJson:$Json
        exit $script:LastChildExitCode
    }
    "health" {
        $selectedProfile = Resolve-Profile -RequestedProfile $Profile -EffectiveAction $effectiveAction
        if (-not $Json) {
            Write-Host "Profile: $selectedProfile"
            if ($NoFrontend) {
                Write-Host "Frontend: excluded by -NoFrontend"
            }
            Write-Section "Runtime health"
        }
        Invoke-ChildScript -ScriptPath $HealthScript -SelectedProfile $selectedProfile -PassNoFrontend:$NoFrontend -PassJson:$Json
        exit $script:LastChildExitCode
    }
    "status" {
        $selectedProfile = Resolve-Profile -RequestedProfile $Profile -EffectiveAction $effectiveAction
        $services = @(Get-ProfileServices -SelectedProfile $selectedProfile -ExcludeFrontend:$NoFrontend)
        $withTranslationProfile = Test-UsesTranslationProfile -Services $services
        $withAvatarProfile = Test-UsesAvatarProfile -Services $services

        Write-Host "Profile: $selectedProfile"
        if ($NoFrontend) {
            Write-Host "Frontend: excluded by -NoFrontend"
        }
        Write-Section "Docker Compose status"
        $composeArgs = Get-ComposeBaseArgs -WithTranslationProfile $withTranslationProfile -WithAvatarProfile $withAvatarProfile
        $composeArgs += "ps"
        $psCode = Invoke-Compose -ComposeArgs $composeArgs

        Write-Section "Runtime health"
        Invoke-ChildScript -ScriptPath $HealthScript -SelectedProfile $selectedProfile -PassNoFrontend:$NoFrontend
        $healthCode = $script:LastChildExitCode
        if ($healthCode -ne 0) {
            Write-Warning "Runtime health exited with code $healthCode."
        }
        exit $psCode
    }
    "stop" {
        $selectedProfile = Resolve-Profile -RequestedProfile $Profile -EffectiveAction $effectiveAction
        $services = @(Get-ProfileServices -SelectedProfile $selectedProfile -ExcludeFrontend:$NoFrontend)
        $withTranslationProfile = Test-UsesTranslationProfile -Services $services
        $withAvatarProfile = Test-UsesAvatarProfile -Services $services

        Write-Host "Profile: $selectedProfile"
        if ($NoFrontend) {
            Write-Host "Frontend: excluded by -NoFrontend"
        }
        Write-Section "Stopping services"
        foreach ($service in $services) {
            Write-Host "- $service"
        }
        Write-Host "Using docker compose stop. Volumes, images, and runtime data are preserved."

        $composeArgs = Get-ComposeBaseArgs -WithTranslationProfile $withTranslationProfile -WithAvatarProfile $withAvatarProfile
        $composeArgs += @("stop")
        $composeArgs += $services
        $code = Invoke-Compose -ComposeArgs $composeArgs
        exit $code
    }
    "start" {
        $selectedProfile = Resolve-Profile -RequestedProfile $Profile -EffectiveAction $effectiveAction
        $services = @(Get-ProfileServices -SelectedProfile $selectedProfile -ExcludeFrontend:$NoFrontend)
        $withTranslationProfile = Test-UsesTranslationProfile -Services $services
        $withAvatarProfile = Test-UsesAvatarProfile -Services $services

        Write-Host "Profile: $selectedProfile"
        if ($NoFrontend) {
            Write-Host "Frontend: excluded by -NoFrontend"
        }
        Write-ProfileWarnings -SelectedProfile $selectedProfile

        if (-not $SkipPreflight) {
            Write-Section "Preflight"
            Invoke-ChildScript -ScriptPath $PreflightScript -SelectedProfile $selectedProfile -PassNoFrontend:$NoFrontend
            $preflightCode = $script:LastChildExitCode
            if ($preflightCode -ne 0) {
                Write-Host ""
                Write-Host "Preflight found core blockers. No services were started."
                exit $preflightCode
            }
        } else {
            Write-Warning "Skipping preflight because -SkipPreflight was supplied."
        }

        Write-Section "Starting services"
        foreach ($service in $services) {
            Write-Host "- $service"
        }
        Write-Host "Compose will use --no-build and --pull never; missing images fail instead of building or pulling."

        $composeArgs = Get-ComposeBaseArgs -WithTranslationProfile $withTranslationProfile -WithAvatarProfile $withAvatarProfile
        $composeArgs += @("up", "-d", "--no-build", "--pull", "never")
        $composeArgs += $services
        $startCode = Invoke-Compose -ComposeArgs $composeArgs
        if ($startCode -ne 0) {
            exit $startCode
        }

        Write-ServiceUrls -Services $services

        if (-not $NoHealth) {
            Write-Section "Runtime health"
            Invoke-ChildScript -ScriptPath $HealthScript -SelectedProfile $selectedProfile -PassNoFrontend:$NoFrontend
            $healthCode = $script:LastChildExitCode
            exit $healthCode
        }

        Write-Host ""
        Write-Host "Skipped runtime health because -NoHealth was supplied."
        exit 0
    }
}
