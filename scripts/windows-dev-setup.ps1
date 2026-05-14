[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipPythonInstall,
    [switch]$SkipNpmInstall
)

$ErrorActionPreference = "Continue"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$FrontendDir = Join-Path $RepoRoot "services\frontend"
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

function Add-Failure {
    param([string]$Message)
    $Failures.Add($Message) | Out-Null
}

function Add-Warning {
    param([string]$Message)
    $Warnings.Add($Message) | Out-Null
}

function Invoke-Step {
    param(
        [string]$Description,
        [scriptblock]$ScriptBlock
    )
    Write-Host $Description
    if ($CheckOnly) {
        Write-Host "CheckOnly: skipped."
        return
    }
    & $ScriptBlock
    if ($LASTEXITCODE -ne 0) {
        Add-Failure "$Description failed."
    }
}

Set-Location $RepoRoot
Write-Host "VISUS VidLab Windows development setup"
Write-Host "Repo root: $RepoRoot"
if ($CheckOnly) {
    Write-Host "Running in read-only CheckOnly mode."
}

Write-Section "Required tools"

if (Test-CommandAvailable "git") {
    & git --version
} else {
    Add-Failure "Git was not found. Install Git for Windows and reopen PowerShell."
}

if (Test-CommandAvailable "docker") {
    & docker --version
    & docker compose version
    if ($LASTEXITCODE -ne 0) {
        Add-Failure "Docker Compose was not available. Install/start Docker Desktop with Compose v2."
    }
} else {
    Add-Failure "Docker was not found. Install Docker Desktop and enable WSL2 integration."
}

if (Test-CommandAvailable "node") {
    & node --version
} else {
    Add-Failure "Node.js was not found. Install Node.js 20+."
}

if (Test-CommandAvailable "npm") {
    & npm --version
} else {
    Add-Failure "npm was not found. Install Node.js 20+."
}

if (Test-CommandAvailable "python") {
    & python --version
} else {
    Add-Failure "Python was not found. Install Python 3.10+ and ensure it is on PATH."
}

Write-Section "Environment files"

if (Test-Path (Join-Path $RepoRoot "infra\.env.example")) {
    Write-Host "Found infra\.env.example"
} else {
    Add-Failure "Missing infra\.env.example."
}

if (Test-Path (Join-Path $RepoRoot "infra\.env")) {
    Write-Host "Found infra\.env"
} else {
    Add-Warning "Missing infra\.env. Create it with: Copy-Item infra\.env.example infra\.env"
}

Write-Section "Python virtual environment"

if (Test-Path $VenvPython) {
    & $VenvPython --version
} elseif ($CheckOnly) {
    Add-Warning "Repo venv is missing. Setup would create .venv."
} elseif (Test-CommandAvailable "python") {
    Invoke-Step "Creating repo .venv" {
        & python -m venv $VenvDir
    }
} else {
    Add-Failure "Cannot create .venv because Python is missing."
}

if ((Test-Path $VenvPython) -and -not $SkipPythonInstall) {
    $requirements = @(
        (Join-Path $RepoRoot "services\api\requirements.txt"),
        (Join-Path $RepoRoot "requirements-test.txt")
    ) | Where-Object { Test-Path $_ }

    if ($requirements.Count -gt 0) {
        if ($CheckOnly) {
            Write-Host "CheckOnly: would install Python requirements:"
            foreach ($req in $requirements) {
                Write-Host "- $req"
            }
        } else {
            & $VenvPython -m pip install --upgrade pip
            if ($LASTEXITCODE -ne 0) {
                Add-Failure "pip upgrade failed."
            }
            foreach ($req in $requirements) {
                Write-Host "Installing $req"
                & $VenvPython -m pip install -r $req
                if ($LASTEXITCODE -ne 0) {
                    Add-Failure "pip install failed for $req."
                }
            }
        }
    } else {
        Add-Warning "No obvious Python requirements files were found."
    }
} elseif ($SkipPythonInstall) {
    Write-Host "Skipping Python dependency install."
}

Write-Section "Frontend dependencies"

$NodeModules = Join-Path $FrontendDir "node_modules"
$PackageLock = Join-Path $FrontendDir "package-lock.json"
if (Test-Path $FrontendDir) {
    if ((Test-Path $NodeModules) -or $SkipNpmInstall) {
        if ($SkipNpmInstall) {
            Write-Host "Skipping npm install."
        } else {
            Write-Host "Found services\frontend\node_modules"
        }
    } elseif (Test-Path $PackageLock) {
        if ($CheckOnly) {
            Write-Host "CheckOnly: would run npm ci in services\frontend."
        } else {
            Push-Location $FrontendDir
            & npm ci
            if ($LASTEXITCODE -ne 0) {
                Add-Failure "npm ci failed in services\frontend."
            }
            Pop-Location
        }
    } else {
        Add-Warning "No package-lock.json found in services\frontend; skipping npm ci."
    }
} else {
    Add-Failure "services\frontend directory is missing."
}

Write-Section "Docker Compose"

if (Test-CommandAvailable "docker") {
    & docker compose -f (Join-Path $RepoRoot "infra\docker-compose.yml") config *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Docker Compose config parsed successfully."
    } else {
        Add-Warning "Docker Compose config did not parse. Check Docker Desktop and infra\.env."
    }
}

Write-Section "Next steps"

Write-Host "Start the default local stack:"
Write-Host "  .\scripts\windows-dev-start.ps1"
Write-Host "Start render/TTS services when needed:"
Write-Host "  .\scripts\windows-dev-start.ps1 -WithTts -WithWorker"
Write-Host "Read docs:"
Write-Host "  docs\INSTALL_WINDOWS.md"
Write-Host "  docs\LOCAL_DEVELOPMENT.md"

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

Write-Host "Setup check completed."
