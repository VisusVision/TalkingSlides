[CmdletBinding()]
param(
    [switch]$Help,
    [switch]$ListActions
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Write-Usage {
    Write-Host "TalkingSlides Setup Assistant compatibility launcher"
    Write-Host ""
    Write-Host "Preferred entry points:"
    Write-Host "  TalkingSlides-Setup.exe"
    Write-Host "  talkingslides-setup check --profile core"
    Write-Host "  python -m tools.setup_assistant"
    Write-Host ""
    Write-Host "Legacy entry point:"
    Write-Host "  .\VISUS-VidLab.bat"
}

function Write-Actions {
    Write-Host "TalkingSlides Setup Assistant"
    Write-Host ""
    Write-Host "System Diagnostics:"
    Write-Host "  talkingslides-setup check"
    Write-Host "  talkingslides-setup check --full --profile tts"
    Write-Host "Reports:"
    Write-Host "  talkingslides-setup report --format json"
    Write-Host "Runtime:"
    Write-Host "  talkingslides-setup runtime status --profile core"
    Write-Host "  talkingslides-setup runtime start --profile tts --no-frontend --confirm"
    Write-Host "  talkingslides-setup runtime stop --profile avatar --confirm"
}

function Resolve-Python {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }
    throw "Python 3.10+ was not found. Download the target-native TalkingSlides Setup Assistant package."
}

Write-Host "Deprecated compatibility entry point: VISUS-VidLab.bat now redirects to TalkingSlides Setup Assistant."

if ($Help) {
    Write-Usage
    exit 0
}
if ($ListActions) {
    Write-Actions
    exit 0
}

$packagedGui = Join-Path $RepoRoot "TalkingSlides-Setup.exe"
if (Test-Path -LiteralPath $packagedGui) {
    & $packagedGui
    exit $LASTEXITCODE
}

$pythonExe = Resolve-Python
Push-Location $RepoRoot
try {
    & $pythonExe -m tools.setup_assistant gui --repository $RepoRoot
    $exitCode = if ($null -eq $LASTEXITCODE) { 1 } else { $LASTEXITCODE }
} finally {
    Pop-Location
}
exit $exitCode
