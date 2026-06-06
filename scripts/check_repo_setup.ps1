[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$RepoVenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Failures = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Test-CommandAvailable {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Set-Location $RepoRoot
Write-Host "AI_ACADEMY setup smoke check"
Write-Host "Repo root: $RepoRoot"

Write-Section "Runtime versions"
if (Test-CommandAvailable "python") {
    & python --version
} else {
    $Failures.Add("Python was not found. Install Python 3.10+ and ensure it is on PATH.")
}

if (Test-Path $RepoVenvPython) {
    $repoVenvVersion = & $RepoVenvPython --version
    Write-Host "Repo venv: $repoVenvVersion"
}

if (Test-CommandAvailable "node") {
    & node --version
} else {
    $Warnings.Add("Node.js was not found. Install Node.js 20+ before running the frontend.")
}

Write-Section "Environment template"
if (Test-Path "infra/.env.example") {
    Write-Host "Found infra/.env.example"
} else {
    $Failures.Add("Missing infra/.env.example.")
}

if (Test-Path "infra/.env") {
    Write-Host "Found infra/.env"
} else {
    $Warnings.Add("Missing infra/.env. Run: cp infra/.env.example infra/.env")
}

Write-Section "Docker Compose config"
if (Test-CommandAvailable "docker") {
    & docker compose -f infra/docker-compose.yml config *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Docker Compose config parsed successfully."
    } else {
        $Failures.Add("Docker Compose config did not parse. Check Docker Desktop, infra/.env, and infra/docker-compose.yml.")
    }
} else {
    $Warnings.Add("Docker was not found. Install/start Docker Desktop to validate Compose.")
}

Write-Section "Django check"
if ((Test-CommandAvailable "python") -or (Test-Path $RepoVenvPython)) {
    $djangoPython = if (Test-CommandAvailable "python") { "python" } else { $RepoVenvPython }
    & $djangoPython -c "import django; print('Django ' + django.get_version())" *> $null
    if (($LASTEXITCODE -ne 0) -and (Test-Path $RepoVenvPython) -and ($djangoPython -ne $RepoVenvPython)) {
        $djangoPython = $RepoVenvPython
        & $djangoPython -c "import django; print('Django ' + django.get_version())" *> $null
    }

    if ($LASTEXITCODE -eq 0) {
        $previousSettings = $env:DJANGO_SETTINGS_MODULE
        $previousPythonPath = $env:PYTHONPATH
        $previousStorageRoot = $env:STORAGE_ROOT
        $env:DJANGO_SETTINGS_MODULE = if ($env:DJANGO_SETTINGS_MODULE) { $env:DJANGO_SETTINGS_MODULE } else { "config.settings" }
        $env:PYTHONPATH = "$RepoRoot\services\api;$RepoRoot\services;$RepoRoot\services\scripts;$RepoRoot\services\tts_service;$previousPythonPath"
        if (-not $env:STORAGE_ROOT) {
            $env:STORAGE_ROOT = "$RepoRoot\storage_local"
        }

        & $djangoPython services/api/manage.py check
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Django manage.py check passed."
        } else {
            $Failures.Add("Django manage.py check failed. Install API dependencies and verify environment variables.")
        }

        $env:DJANGO_SETTINGS_MODULE = $previousSettings
        $env:PYTHONPATH = $previousPythonPath
        $env:STORAGE_ROOT = $previousStorageRoot
    } else {
        $Warnings.Add("Django is not importable. Activate a venv and run: pip install -r services/api/requirements.txt")
    }
}

Write-Section "Optional tools"
if (Test-CommandAvailable "ffmpeg") {
    & ffmpeg -version 2>$null | Select-Object -First 1
} else {
    $Warnings.Add("ffmpeg was not found. Basic API/frontend startup can still work; video rendering checks may need ffmpeg.")
}
$Warnings.Add("GPU, MuseTalk, LivePortrait, and avatar model assets are optional for basic setup and are not checked here.")

Write-Section "Summary"
if ($Warnings.Count -gt 0) {
    Write-Host "Warnings:"
    foreach ($warning in $Warnings) {
        Write-Host "- $warning"
    }
}

if ($Failures.Count -gt 0) {
    Write-Host "Failures:"
    foreach ($failure in $Failures) {
        Write-Host "- $failure"
    }
    exit 1
}

Write-Host "Basic setup smoke check passed."
