[CmdletBinding()]
param(
    [switch]$Json
)

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ComposeFile = Join-Path $RepoRoot "infra\docker-compose.yml"
$EnvFile = Join-Path $RepoRoot "infra\.env"
$EnvExampleFile = Join-Path $RepoRoot "infra\.env.example"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Results = New-Object System.Collections.Generic.List[object]

function Add-Result {
    param(
        [string]$Category,
        [string]$Name,
        [ValidateSet("PASS", "WARN", "FAIL")]
        [string]$Status,
        [string]$Detail,
        [string]$NextStep = ""
    )

    $Results.Add([pscustomobject]@{
        category = $Category
        name = $Name
        status = $Status
        detail = $Detail
        next_step = $NextStep
    }) | Out-Null
}

function Test-CommandAvailable {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-External {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    $output = & $Command @Arguments 2>&1
    return [pscustomobject]@{
        exit_code = $LASTEXITCODE
        output = @($output | ForEach-Object { $_.ToString() -replace "`0", "" })
    }
}

function Format-OutputLine {
    param([object]$CommandResult)
    $text = ($CommandResult.output | Where-Object { $_ } | Select-Object -First 4) -join " | "
    if ([string]::IsNullOrWhiteSpace($text)) {
        return "(no output)"
    }
    return $text
}

function Test-TcpPortOpen {
    param(
        [int]$Port,
        [int]$TimeoutMs = 350
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if (-not $connected) {
            return $false
        }
        $client.EndConnect($async)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-HttpReachable {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 2
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds -Method Get
        return [pscustomobject]@{
            ok = ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 500)
            status_code = [int]$response.StatusCode
            error = ""
        }
    } catch {
        $statusCode = $null
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        return [pscustomobject]@{
            ok = $false
            status_code = $statusCode
            error = $_.Exception.Message
        }
    }
}

function Get-DriveFreeGb {
    param([string]$Path)

    $driveName = ([System.IO.Path]::GetPathRoot($Path)).TrimEnd("\").TrimEnd(":")
    $drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
    if (-not $drive) {
        return $null
    }
    return [math]::Round(($drive.Free / 1GB), 1)
}

function Get-ResultStatus {
    param([string[]]$Categories)

    $matches = @($Results | Where-Object { $Categories -contains $_.category })
    if ($matches | Where-Object { $_.status -eq "FAIL" }) {
        return "FAIL"
    }
    if ($matches | Where-Object { $_.status -eq "WARN" }) {
        return "WARN"
    }
    return "PASS"
}

function Test-ResultPassed {
    param([string]$Name)
    return [bool]($Results | Where-Object { $_.name -eq $Name -and $_.status -eq "PASS" })
}

Set-Location $RepoRoot

$isWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
if ($isWindows) {
    Add-Result "Windows / PowerShell" "Windows OS" "PASS" ([System.Environment]::OSVersion.VersionString)
} else {
    Add-Result "Windows / PowerShell" "Windows OS" "FAIL" ([System.Environment]::OSVersion.VersionString) "Run the Windows setup scripts from Windows."
}

$psVersion = $PSVersionTable.PSVersion.ToString()
if ($PSVersionTable.PSVersion.Major -ge 5) {
    Add-Result "Windows / PowerShell" "PowerShell version" "PASS" $psVersion
} else {
    Add-Result "Windows / PowerShell" "PowerShell version" "FAIL" $psVersion "Use Windows PowerShell 5.1+ or PowerShell 7+."
}

try {
    $policy = Get-ExecutionPolicy
    if ($policy -eq "Restricted") {
        Add-Result "Windows / PowerShell" "Execution policy" "WARN" "Effective policy is Restricted." "Run with: powershell -ExecutionPolicy Bypass -File scripts\windows-preflight.ps1"
    } else {
        Add-Result "Windows / PowerShell" "Execution policy" "PASS" "Effective policy: $policy"
    }
} catch {
    Add-Result "Windows / PowerShell" "Execution policy" "WARN" "Could not read execution policy: $($_.Exception.Message)"
}

if (Test-CommandAvailable "wsl.exe") {
    $wslStatus = Invoke-External "wsl.exe" @("--status")
    if ($wslStatus.exit_code -eq 0) {
        Add-Result "WSL2" "wsl.exe status" "PASS" (Format-OutputLine $wslStatus)
    } else {
        Add-Result "WSL2" "wsl.exe status" "WARN" (Format-OutputLine $wslStatus) "Check WSL2 installation and Docker Desktop WSL integration."
    }
} else {
    Add-Result "WSL2" "wsl.exe" "FAIL" "wsl.exe was not found." "Install/enable WSL2, then enable Docker Desktop WSL integration."
}

$dockerCommandAvailable = Test-CommandAvailable "docker"
if ($dockerCommandAvailable) {
    $dockerVersion = Invoke-External "docker" @("--version")
    if ($dockerVersion.exit_code -eq 0) {
        Add-Result "Docker" "docker command" "PASS" (Format-OutputLine $dockerVersion)
    } else {
        Add-Result "Docker" "docker command" "FAIL" (Format-OutputLine $dockerVersion) "Install or repair Docker Desktop."
    }

    $composeVersion = Invoke-External "docker" @("compose", "version")
    if ($composeVersion.exit_code -eq 0) {
        Add-Result "Docker" "docker compose" "PASS" (Format-OutputLine $composeVersion)
    } else {
        Add-Result "Docker" "docker compose" "FAIL" (Format-OutputLine $composeVersion) "Install Docker Desktop with Compose v2."
    }

    $daemon = Invoke-External "docker" @("version", "--format", "{{.Server.Version}}")
    if ($daemon.exit_code -eq 0) {
        Add-Result "Docker" "Docker daemon" "PASS" "Server version: $(Format-OutputLine $daemon)"
    } else {
        Add-Result "Docker" "Docker daemon" "FAIL" (Format-OutputLine $daemon) "Start Docker Desktop and wait until the daemon is ready."
    }

    $composeConfig = Invoke-External "docker" @("compose", "-f", $ComposeFile, "config", "--quiet")
    if ($composeConfig.exit_code -eq 0) {
        Add-Result "Docker" "Compose config" "PASS" "infra\docker-compose.yml parsed successfully."
    } else {
        Add-Result "Docker" "Compose config" "FAIL" (Format-OutputLine $composeConfig) "Fix Docker Compose configuration or required env-file values."
    }

    $composeServices = Invoke-External "docker" @("compose", "-f", $ComposeFile, "--profile", "translation", "config", "--services")
    if ($composeServices.exit_code -eq 0) {
        $services = @($composeServices.output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        Add-Result "Docker profiles/services" "Compose services" "PASS" ($services -join ", ")
    } else {
        $services = @()
        Add-Result "Docker profiles/services" "Compose services" "WARN" (Format-OutputLine $composeServices) "Run docker compose config after Docker is ready."
    }

    $dockerInfo = Invoke-External "docker" @("info", "--format", "{{json .Runtimes}}")
    if ($dockerInfo.exit_code -eq 0) {
        $runtimeText = (Format-OutputLine $dockerInfo)
        if ($runtimeText -match "nvidia") {
            Add-Result "NVIDIA / GPU optional" "Docker GPU runtime hint" "PASS" "docker info lists an nvidia runtime."
        } else {
            Add-Result "NVIDIA / GPU optional" "Docker GPU runtime hint" "WARN" "docker info did not list an nvidia runtime. This script did not run a GPU container." "Validate Docker GPU support before using avatar-gpu."
        }
    } else {
        Add-Result "NVIDIA / GPU optional" "Docker GPU runtime hint" "WARN" (Format-OutputLine $dockerInfo) "Start Docker Desktop before checking Docker GPU support."
    }
} else {
    $services = @()
    Add-Result "Docker" "docker command" "FAIL" "docker was not found." "Install Docker Desktop and enable WSL2 integration."
    Add-Result "Docker" "docker compose" "FAIL" "docker compose could not be checked because docker is missing." "Install Docker Desktop with Compose v2."
    Add-Result "Docker" "Docker daemon" "FAIL" "Docker daemon could not be checked because docker is missing." "Install/start Docker Desktop."
    Add-Result "Docker" "Compose config" "FAIL" "Compose config could not be checked because docker is missing." "Install/start Docker Desktop."
    Add-Result "Docker profiles/services" "Compose services" "WARN" "Compose services could not be read because docker is missing."
    Add-Result "NVIDIA / GPU optional" "Docker GPU runtime hint" "WARN" "Docker GPU support could not be checked because docker is missing."
}

if (Test-CommandAvailable "git") {
    $gitVersion = Invoke-External "git" @("--version")
    Add-Result "Git" "git command" "PASS" (Format-OutputLine $gitVersion)
} else {
    Add-Result "Git" "git command" "FAIL" "git was not found." "Install Git for Windows and reopen PowerShell."
}

if (Test-CommandAvailable "node") {
    $nodeVersion = Invoke-External "node" @("--version")
    Add-Result "Node / npm" "node command" "PASS" (Format-OutputLine $nodeVersion)
} else {
    Add-Result "Node / npm" "node command" "WARN" "node was not found." "Install Node.js 20+ for frontend development and tests."
}

if (Test-CommandAvailable "npm") {
    $npmVersion = Invoke-External "npm" @("--version")
    Add-Result "Node / npm" "npm command" "PASS" (Format-OutputLine $npmVersion)
} else {
    Add-Result "Node / npm" "npm command" "WARN" "npm was not found." "Install Node.js 20+ for frontend development and tests."
}

if (Test-Path $VenvPython) {
    $venvVersion = Invoke-External $VenvPython @("--version")
    Add-Result "Python" "repo .venv" "PASS" (Format-OutputLine $venvVersion)
} elseif (Test-CommandAvailable "python") {
    $pythonVersion = Invoke-External "python" @("--version")
    Add-Result "Python" "python command" "PASS" (Format-OutputLine $pythonVersion)
} else {
    Add-Result "Python" "python or repo .venv" "WARN" "No repo .venv or python command was found." "Install Python 3.10+ or create .venv for local tools and tests."
}

if (Test-CommandAvailable "nvidia-smi") {
    $gpu = Invoke-External "nvidia-smi" @("--query-gpu=name,driver_version", "--format=csv,noheader")
    if ($gpu.exit_code -eq 0 -and $gpu.output.Count -gt 0) {
        Add-Result "NVIDIA / GPU optional" "nvidia-smi" "PASS" (Format-OutputLine $gpu)
    } else {
        Add-Result "NVIDIA / GPU optional" "nvidia-smi" "WARN" (Format-OutputLine $gpu) "Check NVIDIA driver installation before using avatar-gpu."
    }
} else {
    Add-Result "NVIDIA / GPU optional" "nvidia-smi" "WARN" "nvidia-smi was not found. Core setup can still run without GPU." "Install NVIDIA drivers only if avatar-gpu is needed."
}

$freeGb = Get-DriveFreeGb $RepoRoot
if ($null -eq $freeGb) {
    Add-Result "Disk space" "Repo drive free space" "WARN" "Could not read free disk space for $RepoRoot."
} elseif ($freeGb -lt 30) {
    Add-Result "Disk space" "Repo drive free space" "WARN" "$freeGb GB free. This is below the 30 GB minimum guidance." "Free disk space before running Docker profiles."
} elseif ($freeGb -lt 60) {
    Add-Result "Disk space" "Repo drive free space" "WARN" "$freeGb GB free. Core may fit, but avatar/full-stack runtime should have 60 GB+ free." "Free more space before avatar-gpu or full-stack runs."
} else {
    Add-Result "Disk space" "Repo drive free space" "PASS" "$freeGb GB free."
}

$ports = @(
    @{ port = 3000; name = "frontend"; tier = "core" },
    @{ port = 8000; name = "API"; tier = "core" },
    @{ port = 8001; name = "TTS"; tier = "tts" },
    @{ port = 5432; name = "Postgres"; tier = "core" },
    @{ port = 6379; name = "Redis"; tier = "core" },
    @{ port = 9000; name = "MinIO API"; tier = "core" },
    @{ port = 9001; name = "MinIO console"; tier = "core" },
    @{ port = 11434; name = "Ollama"; tier = "intelligence" },
    @{ port = 5000; name = "LibreTranslate"; tier = "translation" }
)

foreach ($item in $ports) {
    if (Test-TcpPortOpen -Port $item.port) {
        Add-Result "Ports" "$($item.port) $($item.name)" "WARN" "Port $($item.port) already accepts connections on 127.0.0.1. It may be an existing VISUS service or a conflict."
    } else {
        Add-Result "Ports" "$($item.port) $($item.name)" "PASS" "Port $($item.port) did not accept a local TCP connection."
    }
}

if (Test-Path $EnvExampleFile) {
    Add-Result "Env file" "infra\.env.example" "PASS" "Template exists."
} else {
    Add-Result "Env file" "infra\.env.example" "FAIL" "Template is missing." "Restore infra\.env.example."
}

if (Test-Path $EnvFile) {
    Add-Result "Env file" "infra\.env" "PASS" "Local env file exists. Values were not printed."
} else {
    Add-Result "Env file" "infra\.env" "WARN" "Local env file is missing." "Create it with: Copy-Item infra\.env.example infra\.env"
}

$coreServices = @("postgres", "redis", "minio", "api", "frontend")
$ttsServices = @("tts_service")
$workerServices = @("worker")
$avatarServices = @("worker-avatar")
$translationServices = @("libretranslate")

if ($services.Count -gt 0) {
    foreach ($service in $coreServices) {
        $status = if ($services -contains $service) { "PASS" } else { "FAIL" }
        $detail = if ($status -eq "PASS") { "Defined in Compose config." } else { "Not found in Compose config." }
        Add-Result "Docker profiles/services" "core service: $service" $status $detail
    }

    foreach ($service in $ttsServices) {
        $status = if ($services -contains $service) { "PASS" } else { "WARN" }
        $detail = if ($status -eq "PASS") { "Defined in Compose config." } else { "Not found in Compose config." }
        Add-Result "Docker profiles/services" "tts service: $service" $status $detail
    }

    foreach ($service in $workerServices) {
        $status = if ($services -contains $service) { "PASS" } else { "WARN" }
        $detail = if ($status -eq "PASS") { "Defined in Compose config." } else { "Not found in Compose config." }
        Add-Result "Docker profiles/services" "worker service: $service" $status $detail
    }

    foreach ($service in $avatarServices) {
        $status = if ($services -contains $service) { "PASS" } else { "WARN" }
        $detail = if ($status -eq "PASS") { "Defined in Compose config." } else { "Not found in Compose config." }
        Add-Result "Docker profiles/services" "avatar-gpu service: $service" $status $detail
    }

    foreach ($service in $translationServices) {
        $status = if ($services -contains $service) { "PASS" } else { "WARN" }
        $detail = if ($status -eq "PASS") { "Defined in Compose config behind the translation profile." } else { "Not found in Compose config." }
        Add-Result "Docker profiles/services" "translation service: $service" $status $detail
    }
} else {
    Add-Result "Docker profiles/services" "runtime tiers" "WARN" "Could not verify service names from Compose config."
}

Add-Result "Docker profiles/services" "supported runtime tiers" "PASS" "core, tts, worker, avatar-gpu, translation, and intelligence via host Ollama."

$ollamaLocal = Test-HttpReachable "http://localhost:11434/api/tags"
$ollamaHost = Test-HttpReachable "http://host.docker.internal:11434/api/tags"
if ($ollamaLocal.ok -or $ollamaHost.ok) {
    $reachable = @()
    if ($ollamaLocal.ok) { $reachable += "localhost:11434" }
    if ($ollamaHost.ok) { $reachable += "host.docker.internal:11434" }
    Add-Result "Ollama optional" "Ollama reachability" "PASS" ("Reachable via " + ($reachable -join ", "))
} else {
    Add-Result "Ollama optional" "Ollama reachability" "WARN" "Ollama did not respond on localhost:11434 or host.docker.internal:11434. Intelligence will use heuristic/fallback behavior unless Ollama is configured." "Install/start Ollama and pull models only when local LLM enhancement is needed."
}

$summary = @(
    [pscustomobject]@{ name = "Core stack readiness"; status = Get-ResultStatus @("Windows / PowerShell", "WSL2", "Docker", "Git", "Env file") },
    [pscustomobject]@{ name = "TTS readiness"; status = if (Test-ResultPassed "tts service: tts_service") { "PASS" } else { "WARN" } },
    [pscustomobject]@{ name = "Avatar GPU readiness"; status = if ((Test-ResultPassed "avatar-gpu service: worker-avatar") -and (Test-ResultPassed "nvidia-smi")) { "PASS" } else { "WARN" } },
    [pscustomobject]@{ name = "Intelligence/Ollama readiness"; status = Get-ResultStatus @("Ollama optional") },
    [pscustomobject]@{ name = "Translation readiness"; status = if (Test-ResultPassed "translation service: libretranslate") { "PASS" } else { "WARN" } }
)

$hasCoreFailure = [bool]($Results | Where-Object {
    $_.status -eq "FAIL" -and @("Windows / PowerShell", "WSL2", "Docker", "Git", "Env file", "Docker profiles/services") -contains $_.category
})
$exitCode = if ($hasCoreFailure) { 1 } else { 0 }

if ($Json) {
    [pscustomobject]@{
        generated_at = (Get-Date).ToString("o")
        repo_root = $RepoRoot
        results = @($Results.ToArray())
        summary = @($summary)
        exit_code = $exitCode
    } | ConvertTo-Json -Depth 6
    exit $exitCode
}

Write-Host "VISUS VidLab Windows preflight"
Write-Host "Repo root: $RepoRoot"
Write-Host ""

foreach ($group in ($Results | Group-Object category)) {
    Write-Host "== $($group.Name) =="
    foreach ($result in $group.Group) {
        $line = "[{0}] {1}: {2}" -f $result.status, $result.name, $result.detail
        Write-Host $line
        if ($result.next_step) {
            Write-Host "      Next: $($result.next_step)"
        }
    }
    Write-Host ""
}

Write-Host "== Final summary =="
foreach ($item in $summary) {
    Write-Host ("[{0}] {1}" -f $item.status, $item.name)
}

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Core blockers were found. Resolve FAIL items before starting the core stack."
}

exit $exitCode
