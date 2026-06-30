[CmdletBinding()]
param(
    [switch]$Help,
    [switch]$ListActions
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DoctorScript = Join-Path $PSScriptRoot "windows-doctor.ps1"
$RuntimeScript = Join-Path $PSScriptRoot "windows-runtime.ps1"
$HealthScript = Join-Path $PSScriptRoot "windows-runtime-health.ps1"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$FrontendDir = Join-Path $RepoRoot "services\frontend"
$DoctorReportDir = Join-Path $RepoRoot "scratch\doctor-reports"

function Write-Heading {
    param([string]$Message)

    Write-Host ""
    Write-Host "== $Message =="
}

function Write-Menu {
    Write-Host ""
    Write-Host "Welcome to VISUS VidLab"
    Write-Host ""
    Write-Host "[1] Run Doctor"
    Write-Host "[2] Start Core Runtime"
    Write-Host "[3] Start Avatar Runtime"
    Write-Host "[4] Health Check"
    Write-Host "[5] Run Quick Tests"
    Write-Host "[6] Run Full Tests"
    Write-Host "[7] Stop Runtime"
    Write-Host "[8] Open Local URLs"
    Write-Host "[9] Save Doctor Report"
    Write-Host "[0] Exit"
    Write-Host ""
}

function Write-HelpText {
    Write-Host "VISUS VidLab local launcher"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  .\VISUS-VidLab.bat"
    Write-Host "  .\scripts\visus-launcher.ps1"
    Write-Host "  .\scripts\visus-launcher.ps1 -ListActions"
    Write-Host ""
    Write-Host "This launcher is a convenience menu around the existing Windows scripts."
    Write-Host "It does not read local env files, print secret values, fetch packages, fetch models, submit avatar jobs, or run destructive cleanup commands."
    Write-Host "Runtime start and stop actions delegate to scripts\windows-runtime.ps1."
    Write-Host "Doctor and health output keeps the existing PASS/WARN/FAIL format."
}

function Pause-ForMenu {
    Write-Host ""
    [void](Read-Host "Press Enter to return to the menu")
}

function Confirm-Action {
    param(
        [string]$Prompt,
        [switch]$Strong
    )

    if ($Strong) {
        $answer = Read-Host "$Prompt Type YES to continue"
        return $answer -ceq "YES"
    }

    $answer = Read-Host "$Prompt [y/N]"
    return $answer -match "^(?i:y|yes)$"
}

function Show-CommandFailure {
    param(
        [string]$Command,
        [int]$ExitCode,
        [string]$NextSuggestion
    )

    Write-Host ""
    Write-Host "Command did not complete cleanly."
    Write-Host "Command: $Command"
    Write-Host "Exit code: $ExitCode"
    if (-not [string]::IsNullOrWhiteSpace($NextSuggestion)) {
        Write-Host "Next suggestion: $NextSuggestion"
    }
}

function Invoke-PowerShellFile {
    param(
        [string]$DisplayCommand,
        [string]$ScriptPath,
        [string[]]$Arguments = @(),
        [string]$NextSuggestion = "Review the output above, fix blocking FAIL items, and retry.",
        [int[]]$SuccessExitCodes = @(0),
        [switch]$SuppressOutput,
        [string]$TeeOutputPath = ""
    )

    Write-Heading "Running"
    Write-Host "Command: $DisplayCommand"

    Push-Location $RepoRoot
    try {
        $powershellArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath) + $Arguments
        if ($SuppressOutput) {
            & powershell @powershellArgs | Out-Null
        } elseif (-not [string]::IsNullOrWhiteSpace($TeeOutputPath)) {
            & powershell @powershellArgs *>&1 | Tee-Object -FilePath $TeeOutputPath
        } else {
            & powershell @powershellArgs
        }
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
    } catch {
        Write-Host "Could not start command: $($_.Exception.Message)"
        $exitCode = 1
    } finally {
        Pop-Location
    }

    if ($SuccessExitCodes -notcontains $exitCode) {
        Show-CommandFailure -Command $DisplayCommand -ExitCode $exitCode -NextSuggestion $NextSuggestion
    }

    return $exitCode
}

function Invoke-NativeCommand {
    param(
        [string]$DisplayCommand,
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $RepoRoot,
        [string]$NextSuggestion = "Review the output above and retry after fixing the failing test."
    )

    Write-Heading "Running"
    Write-Host "Command: $DisplayCommand"

    Push-Location $WorkingDirectory
    try {
        if (($FilePath -match "[\\/]") -and -not (Test-Path $FilePath)) {
            Write-Host "Command was not found: $FilePath"
            $exitCode = 1
        } else {
            & $FilePath @Arguments
            $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
        }
    } catch {
        Write-Host "Could not start command: $($_.Exception.Message)"
        $exitCode = 1
    } finally {
        Pop-Location
    }

    if ($exitCode -ne 0) {
        Show-CommandFailure -Command $DisplayCommand -ExitCode $exitCode -NextSuggestion $NextSuggestion
    }

    return $exitCode
}

function Read-ProfileChoice {
    param([string]$Purpose)

    Write-Host ""
    Write-Host "Choose profile for ${Purpose}:"
    Write-Host "[1] core"
    Write-Host "[2] avatar"
    Write-Host "[3] full"
    $choice = Read-Host "Profile"

    switch ($choice.ToLowerInvariant()) {
        "1" { return "core" }
        "core" { return "core" }
        "2" { return "avatar" }
        "avatar" { return "avatar" }
        "3" { return "full" }
        "full" { return "full" }
        default {
            Write-Host "Unsupported profile choice. Choose core, avatar, or full."
            return $null
        }
    }
}

function Invoke-Doctor {
    $null = Invoke-PowerShellFile `
        -DisplayCommand ".\scripts\windows-doctor.ps1" `
        -ScriptPath $DoctorScript `
        -NextSuggestion "Review FAIL items. WARN items can be expected for optional providers or avatar readiness."
}

function Start-CoreRuntime {
    $prompt = "Start core services? This may start Docker containers but will not build or pull images."
    if (-not (Confirm-Action -Prompt $prompt)) {
        Write-Host "Core runtime start cancelled."
        return
    }

    $null = Invoke-PowerShellFile `
        -DisplayCommand ".\scripts\windows-runtime.ps1 -Profile core" `
        -ScriptPath $RuntimeScript `
        -Arguments @("-Profile", "core") `
        -NextSuggestion "Run .\scripts\windows-doctor.ps1, then retry core startup after resolving FAIL items."
}

function Start-AvatarRuntime {
    Write-Host ""
    Write-Host "Avatar runtime may consume queued avatar jobs if real avatar queue has jobs. For safety, use only when you intend to process avatar work."
    Write-Host "Consider running .\scripts\windows-avatar-runtime.ps1 -Action Check before starting avatar services."

    $prompt = "Start avatar services? This may start Docker containers but will not build or pull images."
    if (-not (Confirm-Action -Prompt $prompt)) {
        Write-Host "Avatar runtime start cancelled."
        return
    }

    $null = Invoke-PowerShellFile `
        -DisplayCommand ".\scripts\windows-runtime.ps1 -Profile avatar" `
        -ScriptPath $RuntimeScript `
        -Arguments @("-Profile", "avatar") `
        -NextSuggestion "Check avatar readiness and queue intent before retrying. Do not start avatar unless queued work should be processed."
}

function Invoke-HealthCheck {
    $selectedProfile = Read-ProfileChoice -Purpose "health check"
    if ([string]::IsNullOrWhiteSpace($selectedProfile)) {
        return
    }

    $null = Invoke-PowerShellFile `
        -DisplayCommand ".\scripts\windows-runtime-health.ps1 -Profile $selectedProfile" `
        -ScriptPath $HealthScript `
        -Arguments @("-Profile", $selectedProfile) `
        -NextSuggestion "Start or repair the selected runtime profile, then rerun health."
}

function Invoke-QuickTests {
    if (-not (Confirm-Action -Prompt "Run quick backend smoke tests now?")) {
        Write-Host "Quick tests cancelled."
        return
    }

    $null = Invoke-NativeCommand `
        -DisplayCommand ".\.venv\Scripts\python.exe -m pytest tests\test_ci_docker_smoke_contract.py -q" `
        -FilePath $PythonExe `
        -Arguments @("-m", "pytest", "tests\test_ci_docker_smoke_contract.py", "-q") `
        -NextSuggestion "Fix the failing static contract or rerun after activating the local virtual environment."

    if (Confirm-Action -Prompt "Run frontend Vitest too? This uses installed dependencies only.") {
        $null = Invoke-NativeCommand `
            -DisplayCommand "cd services\frontend; npm run test:ci" `
            -FilePath "npm" `
            -Arguments @("run", "test:ci") `
            -WorkingDirectory $FrontendDir `
            -NextSuggestion "Run from services\frontend after ensuring dependencies are already present."
    }
}

function Invoke-FullTests {
    $prompt = "This may take longer. It should not run avatar jobs, but may run many tests."
    if (-not (Confirm-Action -Prompt $prompt -Strong)) {
        Write-Host "Full test run cancelled."
        return
    }

    Write-Heading "Recommended commands"
    Write-Host "Full safe test scope is intentionally review-first in this launcher."
    Write-Host "No full test command was started."
    Write-Host ""
    Write-Host "Suggested non-avatar checks:"
    Write-Host "  .\.venv\Scripts\python.exe -m pytest tests\test_ci_docker_smoke_contract.py -q"
    Write-Host "  cd services\frontend"
    Write-Host "  npm run test:ci"
    Write-Host ""
    Write-Host "Avoid broad render/avatar integration tests unless you explicitly intend to exercise those paths."
}

function Stop-Runtime {
    $selectedProfile = Read-ProfileChoice -Purpose "stop"
    if ([string]::IsNullOrWhiteSpace($selectedProfile)) {
        return
    }

    $prompt = "Stop $selectedProfile runtime services with the safe stop wrapper?"
    if (-not (Confirm-Action -Prompt $prompt)) {
        Write-Host "Stop cancelled."
        return
    }

    $null = Invoke-PowerShellFile `
        -DisplayCommand ".\scripts\windows-runtime.ps1 -Profile $selectedProfile -Stop" `
        -ScriptPath $RuntimeScript `
        -Arguments @("-Profile", $selectedProfile, "-Stop") `
        -NextSuggestion "Check Docker Desktop and rerun health/status if services remain in an unexpected state."
}

function Open-LocalUrls {
    $urls = @(
        [pscustomobject]@{ Name = "Frontend"; Url = "http://localhost:3000" },
        [pscustomobject]@{ Name = "API"; Url = "http://localhost:8000" },
        [pscustomobject]@{ Name = "MinIO Console"; Url = "http://localhost:9001" }
    )

    Write-Heading "Local URLs"
    foreach ($entry in $urls) {
        Write-Host ("{0}: {1}" -f $entry.Name, $entry.Url)
    }
    Write-Host "No credentials are shown by this launcher."

    if (-not (Confirm-Action -Prompt "Open these URLs in your browser?")) {
        Write-Host "Browser open cancelled."
        return
    }

    foreach ($entry in $urls) {
        try {
            Start-Process $entry.Url
        } catch {
            Show-CommandFailure `
                -Command "Start-Process $($entry.Url)" `
                -ExitCode 1 `
                -NextSuggestion "Open the URL manually in your browser."
        }
    }
}

function Save-DoctorReport {
    New-Item -ItemType Directory -Force -Path $DoctorReportDir | Out-Null

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $jsonRelativePath = "scratch\doctor-reports\doctor-$timestamp.json"
    $txtRelativePath = "scratch\doctor-reports\doctor-$timestamp.txt"
    $txtPath = Join-Path $RepoRoot $txtRelativePath

    $jsonCode = Invoke-PowerShellFile `
        -DisplayCommand ".\scripts\windows-doctor.ps1 -Json -OutputPath $jsonRelativePath" `
        -ScriptPath $DoctorScript `
        -Arguments @("-Json", "-OutputPath", $jsonRelativePath) `
        -SuccessExitCodes @(0, 1) `
        -SuppressOutput

    $textCode = Invoke-PowerShellFile `
        -DisplayCommand ".\scripts\windows-doctor.ps1" `
        -ScriptPath $DoctorScript `
        -SuccessExitCodes @(0, 1) `
        -TeeOutputPath $txtPath

    Write-Heading "Reports saved"
    Write-Host "JSON: $jsonRelativePath"
    Write-Host "Text: $txtRelativePath"
    Write-Host "Secret values are never printed by the doctor report."

    if (($jsonCode -ne 0) -or ($textCode -ne 0)) {
        Show-CommandFailure `
            -Command ".\scripts\windows-doctor.ps1" `
            -ExitCode ([Math]::Max($jsonCode, $textCode)) `
            -NextSuggestion "Review FAIL items in the saved report. WARN items may be acceptable for optional capabilities."
    }
}

Set-Location $RepoRoot

if ($Help) {
    Write-HelpText
    exit 0
}

if ($ListActions) {
    Write-Menu
    exit 0
}

while ($true) {
    Write-Menu
    $choice = Read-Host "Select an option"

    switch ($choice) {
        "1" { Invoke-Doctor; Pause-ForMenu }
        "2" { Start-CoreRuntime; Pause-ForMenu }
        "3" { Start-AvatarRuntime; Pause-ForMenu }
        "4" { Invoke-HealthCheck; Pause-ForMenu }
        "5" { Invoke-QuickTests; Pause-ForMenu }
        "6" { Invoke-FullTests; Pause-ForMenu }
        "7" { Stop-Runtime; Pause-ForMenu }
        "8" { Open-LocalUrls; Pause-ForMenu }
        "9" { Save-DoctorReport; Pause-ForMenu }
        "0" {
            Write-Host "Exiting VISUS VidLab launcher."
            exit 0
        }
        default {
            Write-Host "Unknown option. Choose 0 through 9."
            Pause-ForMenu
        }
    }
}
