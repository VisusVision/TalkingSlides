[CmdletBinding()]
param(
    [switch]$Json,
    [string]$OutputPath = "",
    [ValidateSet("core", "worker", "tts", "avatar", "translation", "full")]
    [string]$Profile = "core"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ProfileMap = @{
    core = "core"
    worker = "core"
    tts = "tts"
    avatar = "avatar"
    translation = "core"
    full = "avatar"
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
    throw "Python 3.10+ was not found. Use the packaged talkingslides-setup companion command instead."
}

if (-not $Json) {
    Write-Host "Deprecated compatibility command: Windows Doctor is now TalkingSlides Setup Assistant System Diagnostics."
}

$pythonExe = Resolve-Python
$mappedProfile = $ProfileMap[$Profile]
$arguments = @("-m", "tools.setup_assistant")

if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $target = $OutputPath
    if (-not [System.IO.Path]::IsPathRooted($target)) {
        $target = Join-Path $RepoRoot $target
    }
    $arguments += @(
        "report",
        "--full",
        "--profile",
        $mappedProfile,
        "--repository",
        $RepoRoot,
        "--format",
        "json",
        "--output",
        $target
    )
} else {
    $arguments += @(
        "check",
        "--full",
        "--profile",
        $mappedProfile,
        "--repository",
        $RepoRoot
    )
    if ($Json) {
        $arguments += "--json"
    }
}

Push-Location $RepoRoot
try {
    & $pythonExe @arguments
    $exitCode = if ($null -eq $LASTEXITCODE) { 1 } else { $LASTEXITCODE }
} finally {
    Pop-Location
}
exit $exitCode
