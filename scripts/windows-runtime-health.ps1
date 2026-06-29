[CmdletBinding()]
param(
    [string]$Profile = "",
    [switch]$Json,
    [int]$TimeoutSeconds = 3
)

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ComposeFile = Join-Path $RepoRoot "infra\docker-compose.yml"
$AvatarModelRoot = Join-Path $RepoRoot "storage_local\models"
$WorkerImageName = "ai_academy_worker:local"
$ValidProfiles = @("core", "worker", "tts", "avatar", "translation", "full")
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
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutMs = 350
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
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

function Test-HttpEndpoint {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 3
    )

    try {
        $uri = [System.Uri]$Url
        $port = $uri.Port
        if ($port -lt 0) {
            $port = if ($uri.Scheme -eq "https") { 443 } else { 80 }
        }
        if (-not (Test-TcpPortOpen -HostName $uri.Host -Port $port)) {
            return [pscustomobject]@{
                ok = $false
                status_code = $null
                error = "TCP port $port on $($uri.Host) is not accepting connections"
            }
        }

        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds -Method Get
        return [pscustomobject]@{
            ok = ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 400)
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

function ConvertFrom-ComposePsJson {
    param([string[]]$Lines)

    $raw = ($Lines | Where-Object { $_ }) -join "`n"
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return @()
    }

    try {
        $parsed = $raw | ConvertFrom-Json
        if ($parsed -is [System.Array]) {
            return @($parsed)
        }
        return @($parsed)
    } catch {
        $items = @()
        foreach ($line in ($Lines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
            try {
                $items += @($line | ConvertFrom-Json)
            } catch {
            }
        }
        return $items
    }
}

function Get-ServiceStateDetail {
    param([object]$Service)

    $state = ""
    $health = ""
    if ($Service.PSObject.Properties.Name -contains "State") {
        $state = [string]$Service.State
    }
    if ($Service.PSObject.Properties.Name -contains "Health") {
        $health = [string]$Service.Health
    }
    if ([string]::IsNullOrWhiteSpace($health)) {
        return $state
    }
    return "$state ($health)"
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

function Get-CoreHealthStatus {
    $coreMatches = @($Results | Where-Object {
        ($_.category -eq "HTTP endpoints" -and @("frontend", "API health", "API readiness", "capabilities") -contains $_.name) -or
        ($_.category -eq "Docker services" -and @("docker command", "docker compose ps", "api", "frontend", "postgres", "redis", "minio") -contains $_.name)
    })

    if ($coreMatches | Where-Object { $_.status -eq "FAIL" }) {
        return "FAIL"
    }
    if ($coreMatches | Where-Object { $_.status -eq "WARN" }) {
        return "WARN"
    }
    return "PASS"
}

function Resolve-Profile {
    param([string]$RequestedProfile)

    if ([string]::IsNullOrWhiteSpace($RequestedProfile)) {
        Add-Result "Runtime profile" "selected profile" "PASS" "core"
        return "core"
    }

    $normalized = $RequestedProfile.ToLowerInvariant()
    if ($ValidProfiles -notcontains $normalized) {
        Add-Result "Runtime profile" "selected profile" "FAIL" "Unsupported profile '$RequestedProfile'." "Choose one of: $($ValidProfiles -join ', ')."
        return "core"
    }
    Add-Result "Runtime profile" "selected profile" "PASS" $normalized
    return $normalized
}

function Test-UsesTranslationProfile {
    param([string]$SelectedProfile)
    return $SelectedProfile -in @("translation", "full")
}

function Test-UsesAvatarProfile {
    param([string]$SelectedProfile)
    return $SelectedProfile -in @("avatar", "full")
}

function Get-ComposeBaseArgs {
    param([string]$SelectedProfile)

    $args = @("compose", "-f", $ComposeFile)
    if (Test-UsesTranslationProfile -SelectedProfile $SelectedProfile) {
        $args += @("--profile", "translation")
    }
    if (Test-UsesAvatarProfile -SelectedProfile $SelectedProfile) {
        $args += @("--profile", "avatar")
    }
    return $args
}

function Add-AvatarRuntimeReadinessChecks {
    param(
        [bool]$DockerAvailable,
        [bool]$AvatarRequested
    )

    if (-not $AvatarRequested) {
        Add-Result "Avatar runtime" "avatar health checks" "PASS" "Skipped because profile '$SelectedProfile' does not request avatar."
        return
    }

    Add-Result "Avatar runtime" "avatar health checks" "PASS" "Avatar profile requested; checks are read-only and do not start containers."

    if ($DockerAvailable) {
        $imageInspect = Invoke-External "docker" @("image", "inspect", $WorkerImageName, "--format", "{{.Id}} {{.Created}}")
        if ($imageInspect.exit_code -eq 0) {
            Add-Result "Avatar runtime" "worker image" "PASS" "$WorkerImageName exists."
            $history = Invoke-External "docker" @("history", "--no-trunc", $WorkerImageName)
            if ($history.exit_code -eq 0) {
                $historyText = ($history.output | Where-Object { $_ }) -join " "
                if ($historyText -match "INSTALL_OPENMMLAB_DEPS=0|DOWNLOAD_LIVEPORTRAIT_WEIGHTS=0|Skipping OpenMMLab/mmcv|Skipping LivePortrait pretrained weights") {
                    Add-Result "Avatar runtime" "worker image heavy deps" "WARN" "Image history contains smoke/light markers such as INSTALL_OPENMMLAB_DEPS=0 or DOWNLOAD_LIVEPORTRAIT_WEIGHTS=0." "Use a heavy avatar image before starting worker-avatar."
                } elseif ($historyText -match "INSTALL_OPENMMLAB_DEPS=1|DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1") {
                    Add-Result "Avatar runtime" "worker image heavy deps" "PASS" "Image history does not show known smoke/light skip markers."
                } else {
                    Add-Result "Avatar runtime" "worker image heavy deps" "WARN" "Image history did not prove whether OpenMMLab and LivePortrait weights were included."
                }
            } else {
                Add-Result "Avatar runtime" "worker image heavy deps" "WARN" (Format-OutputLine $history) "Could not inspect image history."
            }
            Add-Result "Avatar runtime" "OpenMMLab import proof" "WARN" "mmcv/mmpose/mmdet imports were not run because health does not start containers or heavy runtime checks."
        } else {
            Add-Result "Avatar runtime" "worker image" "WARN" "$WorkerImageName was not found locally." "Build or pull/tag an avatar-capable worker image later; this health check does not build or pull."
        }
    } else {
        Add-Result "Avatar runtime" "worker image" "WARN" "Docker is unavailable, so the local worker image could not be checked."
    }

    $requiredMuseTalkFiles = @(
        "musetalk\musetalk.json",
        "sd-vae\config.json",
        "sd-vae\diffusion_pytorch_model.bin",
        "musetalkV15\unet.pth",
        "whisper\config.json",
        "whisper\pytorch_model.bin",
        "whisper\preprocessor_config.json",
        "dwpose\dw-ll_ucoco_384.pth",
        "face-parse-bisent\79999_iter.pth",
        "face-parse-bisent\resnet18-5c106cde.pth"
    )
    $missingMuseTalk = @()
    foreach ($relativePath in $requiredMuseTalkFiles) {
        if (-not (Test-Path (Join-Path $AvatarModelRoot $relativePath))) {
            $missingMuseTalk += $relativePath
        }
    }
    if ($missingMuseTalk.Count -eq 0) {
        Add-Result "Avatar runtime" "MuseTalk model bundle" "PASS" "Required files were found under storage_local\models."
    } else {
        Add-Result "Avatar runtime" "MuseTalk model bundle" "WARN" "Missing required files under storage_local\models count=$($missingMuseTalk.Count)." "Provision the MuseTalk model bundle before starting worker-avatar."
    }

    $livePortraitModelRoot = Join-Path $AvatarModelRoot "liveportrait"
    if (Test-Path $livePortraitModelRoot) {
        Add-Result "Avatar runtime" "LivePortrait local model bundle" "PASS" "storage_local\models\liveportrait exists."
    } else {
        Add-Result "Avatar runtime" "LivePortrait local model bundle" "WARN" "storage_local\models\liveportrait was not found. Build-time /opt/liveportrait weights may still satisfy the runtime if the image was built with DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1."
    }
}

Set-Location $RepoRoot

$SelectedProfile = Resolve-Profile -RequestedProfile $Profile
$AvatarRequested = Test-UsesAvatarProfile -SelectedProfile $SelectedProfile

$endpoints = @(
    @{ category = "HTTP endpoints"; name = "frontend"; url = "http://localhost:3000"; required = $true; next = ".\scripts\windows-dev-start.ps1" },
    @{ category = "HTTP endpoints"; name = "API health"; url = "http://localhost:8000/health/"; required = $true; next = ".\scripts\windows-dev-start.ps1" },
    @{ category = "HTTP endpoints"; name = "API readiness"; url = "http://localhost:8000/api/v1/ready/"; required = $true; next = ".\scripts\windows-dev-start.ps1" },
    @{ category = "HTTP endpoints"; name = "capabilities"; url = "http://localhost:8000/api/v1/capabilities/"; required = $true; next = ".\scripts\windows-dev-start.ps1" },
    @{ category = "HTTP endpoints"; name = "TTS"; url = "http://localhost:8001/ready"; required = $false; next = ".\scripts\windows-dev-start.ps1 -WithTts" },
    @{ category = "HTTP endpoints"; name = "LibreTranslate"; url = "http://localhost:5000/languages"; required = $false; next = "docker compose -f infra\docker-compose.yml --profile translation up -d libretranslate" },
    @{ category = "HTTP endpoints"; name = "Ollama"; url = "http://localhost:11434/api/tags"; required = $false; next = "Start host-side Ollama only if local LLM enhancement is needed." }
)

foreach ($endpoint in $endpoints) {
    $check = Test-HttpEndpoint -Url $endpoint.url -TimeoutSeconds $TimeoutSeconds
    if ($check.ok) {
        Add-Result $endpoint.category $endpoint.name "PASS" "$($endpoint.url) returned HTTP $($check.status_code)."
    } else {
        $status = if ($endpoint.required) { "FAIL" } else { "WARN" }
        $detail = if ($null -ne $check.status_code) {
            "$($endpoint.url) returned HTTP $($check.status_code)."
        } else {
            "$($endpoint.url) did not respond: $($check.error)"
        }
        Add-Result $endpoint.category $endpoint.name $status $detail $endpoint.next
    }
}

$composeItems = @()
$composeText = @()
$dockerCommandAvailable = Test-CommandAvailable "docker"
if ($dockerCommandAvailable) {
    $composeArgs = @(Get-ComposeBaseArgs -SelectedProfile $SelectedProfile)
    $composePsJson = Invoke-External "docker" ($composeArgs + @("ps", "--format", "json"))
    if ($composePsJson.exit_code -eq 0) {
        Add-Result "Docker services" "docker compose ps" "PASS" "Compose ps completed."
        $composeItems = @(ConvertFrom-ComposePsJson $composePsJson.output)
        $composeText = @($composePsJson.output)
    } else {
        $composePsText = Invoke-External "docker" ($composeArgs + @("ps", "--all"))
        if ($composePsText.exit_code -eq 0) {
            Add-Result "Docker services" "docker compose ps" "WARN" "JSON format was unavailable; using text output."
            $composeText = @($composePsText.output)
        } else {
            Add-Result "Docker services" "docker compose ps" "FAIL" (Format-OutputLine $composePsText) "Start Docker Desktop and check Compose state."
        }
    }
} else {
    Add-Result "Docker services" "docker command" "FAIL" "docker was not found." "Install/start Docker Desktop."
}

$expectedServices = @(
    @{ name = "api"; required = $true },
    @{ name = "frontend"; required = $true },
    @{ name = "postgres"; required = $true },
    @{ name = "redis"; required = $true },
    @{ name = "minio"; required = $true }
)
if ($SelectedProfile -in @("worker", "tts", "avatar", "full")) {
    $expectedServices += @{ name = "worker"; required = $false }
}
if ($SelectedProfile -in @("tts", "avatar", "full")) {
    $expectedServices += @{ name = "tts_service"; required = $false }
}
if ($AvatarRequested) {
    $expectedServices += @{ name = "worker-avatar"; required = $false }
}
if ($SelectedProfile -in @("translation", "full")) {
    $expectedServices += @{ name = "libretranslate"; required = $false }
}

foreach ($expected in $expectedServices) {
    $serviceName = $expected.name
    $service = $null
    if ($composeItems.Count -gt 0) {
        $service = $composeItems | Where-Object {
            ($_.PSObject.Properties.Name -contains "Service" -and $_.Service -eq $serviceName) -or
            ($_.PSObject.Properties.Name -contains "Name" -and $_.Name -match "(^|[-_])$([regex]::Escape($serviceName))([-_]|$)")
        } | Select-Object -First 1
    }

    if ($service) {
        $stateDetail = Get-ServiceStateDetail $service
        if ($stateDetail -match "running|healthy" -and $stateDetail -notmatch "unhealthy|exited|dead") {
            Add-Result "Docker services" $serviceName "PASS" "Present: $stateDetail"
        } else {
            $status = if ($expected.required) { "FAIL" } else { "WARN" }
            Add-Result "Docker services" $serviceName $status "Present but not healthy/running: $stateDetail"
        }
        continue
    }

    $textMatch = $false
    if ($composeText.Count -gt 0) {
        $textMatch = [bool]($composeText | Where-Object { $_ -match [regex]::Escape($serviceName) })
    }

    if ($textMatch) {
        Add-Result "Docker services" $serviceName "WARN" "Found in docker compose ps text output; inspect state manually."
    } else {
        $status = if ($expected.required) { "FAIL" } else { "WARN" }
        Add-Result "Docker services" $serviceName $status "Not present in docker compose ps output."
    }
}

Add-AvatarRuntimeReadinessChecks -DockerAvailable $dockerCommandAvailable -AvatarRequested $AvatarRequested

$avatarReadinessStatus = if (-not $AvatarRequested) { "PASS" } else { Get-ResultStatus @("Avatar runtime") }

$summary = @(
    [pscustomobject]@{ name = "Core stack readiness"; status = Get-CoreHealthStatus },
    [pscustomobject]@{ name = "TTS readiness"; status = if (($Results | Where-Object { $_.name -eq "TTS" -and $_.status -eq "PASS" }) -or ($Results | Where-Object { $_.name -eq "tts_service" -and $_.status -eq "PASS" })) { "PASS" } else { "WARN" } },
    [pscustomobject]@{ name = "Avatar GPU readiness"; status = $avatarReadinessStatus },
    [pscustomobject]@{ name = "Intelligence/Ollama readiness"; status = if ($Results | Where-Object { $_.name -eq "Ollama" -and $_.status -eq "PASS" }) { "PASS" } else { "WARN" } },
    [pscustomobject]@{ name = "Translation readiness"; status = if (($Results | Where-Object { $_.name -eq "LibreTranslate" -and $_.status -eq "PASS" }) -or ($Results | Where-Object { $_.name -eq "libretranslate" -and $_.status -eq "PASS" })) { "PASS" } else { "WARN" } }
)

$hasCoreFailure = ((Get-CoreHealthStatus) -eq "FAIL") -or [bool]($Results | Where-Object { $_.category -eq "Runtime profile" -and $_.status -eq "FAIL" })
$exitCode = if ($hasCoreFailure) { 1 } else { 0 }

if ($Json) {
    [pscustomobject]@{
        generated_at = (Get-Date).ToString("o")
        repo_root = $RepoRoot
        selected_profile = $SelectedProfile
        avatar_checks_enabled = [bool]$AvatarRequested
        results = @($Results.ToArray())
        summary = @($summary)
        exit_code = $exitCode
    } | ConvertTo-Json -Depth 6
    exit $exitCode
}

Write-Host "VISUS VidLab runtime health"
Write-Host "Repo root: $RepoRoot"
Write-Host "Profile: $SelectedProfile"
Write-Host "No services were started, rebuilt, or pulled."
Write-Host ""

foreach ($group in ($Results | Group-Object category)) {
    Write-Host "== $($group.Name) =="
    foreach ($result in $group.Group) {
        Write-Host ("[{0}] {1}: {2}" -f $result.status, $result.name, $result.detail)
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
    Write-Host "Core runtime health failed. Start or repair the core stack before using the full app."
}

exit $exitCode
