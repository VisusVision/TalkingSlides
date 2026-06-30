[CmdletBinding()]
param(
    [switch]$Json,
    [string]$OutputPath = "",
    [ValidateSet("core", "worker", "tts", "avatar", "translation", "full")]
    [string]$Profile = "core"
)

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ComposeFile = Join-Path $RepoRoot "infra\docker-compose.yml"
$EnvFile = Join-Path $RepoRoot "infra\.env"
$EnvExampleFile = Join-Path $RepoRoot "infra\.env.example"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$AvatarModelRoot = Join-Path $RepoRoot "storage_local\models"
$TtsCacheRoot = Join-Path $RepoRoot "storage_local\tts_cache"
$WorkerImageName = "ai_academy_worker:local"
$Results = New-Object System.Collections.Generic.List[object]

$RequiredCoreEnv = @(
    "DJANGO_SETTINGS_MODULE",
    "SECRET_KEY",
    "DEBUG",
    "ALLOWED_HOSTS",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "STORAGE_BACKEND",
    "STORAGE_ROOT",
    "MEDIA_TOKEN_SECRET",
    "VITE_API_BASE_URL"
)

$RecommendedCoreEnv = @(
    "CSRF_TRUSTED_ORIGINS",
    "CORS_ALLOWED_ORIGINS",
    "API_PUBLIC_BASE_URL",
    "TTS_SERVICE_URL"
)

$ProductionRequiredEnv = @(
    "SECRET_KEY",
    "POSTGRES_HOST",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "REDIS_URL",
    "MEDIA_TOKEN_SECRET",
    "ALLOWED_HOSTS",
    "CSRF_TRUSTED_ORIGINS",
    "CORS_ALLOWED_ORIGINS",
    "API_PUBLIC_BASE_URL",
    "VITE_API_BASE_URL"
)

$ProviderGroups = [ordered]@{
    google_oauth = @("GOOGLE_AUTH_ENABLED", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI", "GOOGLE_REDIRECT_SUCCESS_URL")
    email = @("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM_EMAIL", "DEFAULT_FROM_EMAIL", "BREVO_API_KEY", "MAILJET_API_KEY", "MAILJET_SECRET_KEY")
    openai = @("OPENAI_API_KEY", "OPENAI_LESSON_INTELLIGENCE_MODEL", "OPENAI_ANALYTICS_INTELLIGENCE_MODEL")
    ollama = @("ENABLE_LOCAL_OLLAMA", "OLLAMA_BASE_URL", "OLLAMA_LESSON_INTELLIGENCE_MODEL", "OLLAMA_ANALYTICS_INTELLIGENCE_MODEL", "OLLAMA_TRANSLATION_BASE_URL", "OLLAMA_TRANSLATION_MODEL")
    libretranslate = @("LIBRETRANSLATE_BASE_URL", "LIBRETRANSLATE_API_KEY")
    avatar = @("ENABLE_AVATAR", "AVATAR_ENGINE", "MUSETALK_MODEL_PATH", "AVATAR_LIVEPORTRAIT_MODEL_PATH", "AVATAR_LIVEPORTRAIT_CMD", "AVATAR_MUSETALK_CMD")
    s3 = @("STORAGE_BACKEND", "S3_ENDPOINT_URL", "S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_REGION_NAME", "S3_KEY_PREFIX")
    drm = @("DRM_ENABLED", "DRM_LICENSE_URL", "DRM_WIDEVINE_LICENSE_URL", "DRM_PLAYREADY_LICENSE_URL", "DRM_FAIRPLAY_LICENSE_URL", "DRM_CERTIFICATE_URL")
}

function Add-Result {
    param(
        [string]$Category,
        [string]$Name,
        [ValidateSet("PASS", "WARN", "FAIL")]
        [string]$Status,
        [string]$Detail,
        [string]$NextStep = "",
        [string[]]$Variables = @()
    )

    $Results.Add([pscustomobject]@{
        category = $Category
        name = $Name
        status = $Status
        detail = $Detail
        next_step = $NextStep
        variables = @($Variables)
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

    if ([string]::IsNullOrWhiteSpace($Url)) {
        return [pscustomobject]@{ ok = $false; status_code = $null; error = "URL is not configured" }
    }

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

function Read-EnvFile {
    param([string]$Path)

    $map = @{}
    if (-not (Test-Path $Path)) {
        return $map
    }

    foreach ($line in (Get-Content $Path)) {
        if ($line -match '^\s*#' -or [string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $name = $Matches[1].Trim()
            $value = $Matches[2]
            $map[$name] = $value
        }
    }
    return $map
}

function Get-EnvText {
    param(
        [hashtable]$EnvMap,
        [string]$Name,
        [string]$Default = ""
    )

    if ($EnvMap.ContainsKey($Name)) {
        return [string]$EnvMap[$Name]
    }
    return $Default
}

function Test-EnvTruthy {
    param(
        [hashtable]$EnvMap,
        [string]$Name,
        [bool]$Default = $false
    )

    if (-not $EnvMap.ContainsKey($Name)) {
        return $Default
    }
    return ([string]$EnvMap[$Name]).Trim().ToLowerInvariant() -in @("1", "true", "yes", "on")
}

function Test-PlaceholderValue {
    param([string]$Value)

    $trimmed = ([string]$Value).Trim().Trim('"').Trim("'")
    if ([string]::IsNullOrWhiteSpace($trimmed)) {
        return $false
    }
    return [bool]($trimmed -match '^(replace-with|change-me|your-|example\.|placeholder|dev-insecure-secret-key-change-me|media-token-dev-secret-change-in-prod)' -or $trimmed -match '<[^>]+>')
}

function Get-EnvPresence {
    param(
        [hashtable]$EnvMap,
        [string[]]$Names
    )

    $missing = @()
    $blank = @()
    $placeholder = @()
    $present = @()

    foreach ($name in $Names) {
        if (-not $EnvMap.ContainsKey($name)) {
            $missing += $name
            continue
        }
        $value = [string]$EnvMap[$name]
        if ([string]::IsNullOrWhiteSpace($value)) {
            $blank += $name
            continue
        }
        $present += $name
        if (Test-PlaceholderValue $value) {
            $placeholder += $name
        }
    }

    return [pscustomobject]@{
        missing = @($missing)
        blank = @($blank)
        placeholder = @($placeholder)
        present = @($present)
    }
}

function Add-EnvPresenceResult {
    param(
        [string]$Category,
        [string]$Name,
        [hashtable]$EnvMap,
        [string[]]$Names,
        [ValidateSet("PASS", "WARN", "FAIL")]
        [string]$MissingStatus = "WARN",
        [ValidateSet("PASS", "WARN", "FAIL")]
        [string]$PlaceholderStatus = "WARN",
        [string]$NextStep = ""
    )

    $presence = Get-EnvPresence -EnvMap $EnvMap -Names $Names
    $problemNames = @($presence.missing + $presence.blank)
    if ($problemNames.Count -gt 0) {
        Add-Result $Category $Name $MissingStatus "present=$($presence.present.Count) missing=$($presence.missing.Count) blank=$($presence.blank.Count). Values were not printed." $NextStep $problemNames
        return
    }
    if ($presence.placeholder.Count -gt 0 -and $PlaceholderStatus -ne "PASS") {
        Add-Result $Category $Name $PlaceholderStatus "All variables are present, but placeholder-like values remain. Values were not printed." $NextStep $presence.placeholder
        return
    }
    Add-Result $Category $Name "PASS" "All $($Names.Count) variables are present. Values were not printed." "" $Names
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

function Add-ServiceShapeResults {
    param(
        [string[]]$DefaultServices,
        [string[]]$ProfiledServices
    )

    $coreServices = @("postgres", "redis", "minio", "api", "frontend")
    $missingCore = @($coreServices | Where-Object { $DefaultServices -notcontains $_ })
    if ($missingCore.Count -eq 0) {
        Add-Result "Docker/runtime" "core services" "PASS" "Default Compose services include postgres, redis, minio, api, and frontend."
    } else {
        Add-Result "Docker/runtime" "core services" "FAIL" "Default Compose services are missing core service(s). Values were not printed." "Fix infra\docker-compose.yml." $missingCore
    }

    if ($ProfiledServices -contains "worker-avatar") {
        Add-Result "Docker/runtime" "avatar profile service" "PASS" "worker-avatar is available when the avatar profile is selected."
    } else {
        Add-Result "Docker/runtime" "avatar profile service" "FAIL" "worker-avatar was not found in profiled Compose services." "Keep worker-avatar behind the avatar Compose profile."
    }

    if ($DefaultServices -contains "worker-avatar") {
        Add-Result "Docker/runtime" "avatar default isolation" "FAIL" "worker-avatar appears in default Compose services." "Keep worker-avatar excluded from default/core startup."
    } elseif ($ProfiledServices -contains "worker-avatar") {
        Add-Result "Docker/runtime" "avatar default isolation" "PASS" "worker-avatar is excluded from default Compose services and available through --profile avatar."
    } else {
        Add-Result "Docker/runtime" "avatar default isolation" "WARN" "Could not prove worker-avatar profile isolation."
    }
}

function Add-AvatarModelChecks {
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
        Add-Result "Models/assets" "MuseTalk model bundle" "PASS" "Required MuseTalk files were found under storage_local\models."
    } else {
        Add-Result "Models/assets" "MuseTalk model bundle" "WARN" "Missing required MuseTalk files under storage_local\models count=$($missingMuseTalk.Count). No download was attempted." "Provision the model bundle before starting worker-avatar." $missingMuseTalk
    }

    $livePortraitModelRoot = Join-Path $AvatarModelRoot "liveportrait"
    if (Test-Path $livePortraitModelRoot) {
        Add-Result "Models/assets" "LivePortrait local model bundle" "PASS" "storage_local\models\liveportrait exists."
    } else {
        Add-Result "Models/assets" "LivePortrait local model bundle" "WARN" "storage_local\models\liveportrait was not found. Build-time /opt/liveportrait weights may still satisfy worker-avatar."
    }

    $calmTemplate = Join-Path $RepoRoot "storage_local\avatar_templates\calm_lecture_driver.mp4"
    if (Test-Path $calmTemplate) {
        Add-Result "Models/assets" "avatar calm template" "PASS" "Configured calm lecture template path exists."
    } else {
        Add-Result "Models/assets" "avatar calm template" "WARN" "Optional calm lecture template was not found. Avatar image routing may use vetted fallback if enabled."
    }

    $ttsPaths = @(
        $TtsCacheRoot,
        (Join-Path $TtsCacheRoot "hf"),
        (Join-Path $TtsCacheRoot "torch"),
        (Join-Path $TtsCacheRoot "coqui")
    )
    $missingTtsPaths = @($ttsPaths | Where-Object { -not (Test-Path $_) })
    if ($missingTtsPaths.Count -eq 0) {
        Add-Result "Models/assets" "TTS cache paths" "PASS" "Known TTS cache directories exist under storage_local\tts_cache."
    } else {
        Add-Result "Models/assets" "TTS cache paths" "WARN" "Known TTS cache directories are missing count=$($missingTtsPaths.Count). No model download was attempted." "Create/provision caches only when TTS is selected."
    }
}

function Add-OptionalProviderChecks {
    param(
        [hashtable]$EnvMap,
        [bool]$DockerDaemonReady,
        [bool]$WorkerImageExists,
        [bool]$MuseTalkModelsPresent
    )

    $googleEnabled = Test-EnvTruthy -EnvMap $EnvMap -Name "GOOGLE_AUTH_ENABLED" -Default $false
    if ($googleEnabled) {
        Add-EnvPresenceResult "Optional providers" "Google OAuth" $EnvMap @("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI") "WARN" "WARN" "Google sign-in is enabled but OAuth config is incomplete."
    } else {
        Add-Result "Optional providers" "Google OAuth" "WARN" "Google sign-in disabled. Create OAuth credentials in Google Cloud Console if needed." "" $ProviderGroups.google_oauth
    }

    $emailNames = $ProviderGroups.email
    $emailPresence = Get-EnvPresence -EnvMap $EnvMap -Names $emailNames
    if ($emailPresence.present.Count -gt 0) {
        Add-Result "Optional providers" "SMTP/Brevo/Mailjet" "PASS" "At least one email provider variable is present. Values were not printed." "" $emailPresence.present
    } else {
        Add-Result "Optional providers" "SMTP/Brevo/Mailjet" "WARN" "Email sending disabled. Configure SMTP/Brevo/Mailjet." "" $emailNames
    }

    $openAiPresence = Get-EnvPresence -EnvMap $EnvMap -Names @("OPENAI_API_KEY")
    if ($openAiPresence.present.Count -gt 0 -and $openAiPresence.placeholder.Count -eq 0) {
        Add-Result "Optional providers" "OpenAI" "PASS" "OPENAI_API_KEY is present. Value was not printed." "" @("OPENAI_API_KEY")
    } else {
        Add-Result "Optional providers" "OpenAI" "WARN" "Cloud AI disabled. Use Ollama/local fallback if configured." "" @("OPENAI_API_KEY")
    }

    $lessonChain = Get-EnvText $EnvMap "LESSON_INTELLIGENCE_PROVIDER_CHAIN" ""
    $analyticsChain = Get-EnvText $EnvMap "ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN" ""
    $translationChain = Get-EnvText $EnvMap "SUBTITLE_TRANSLATION_PROVIDER_CHAIN" ""
    $ollamaRequested = (Test-EnvTruthy -EnvMap $EnvMap -Name "ENABLE_LOCAL_OLLAMA" -Default $false) -or ($lessonChain -match "(^|[, ])ollama($|[, ])") -or ($analyticsChain -match "(^|[, ])ollama($|[, ])") -or ($translationChain -match "(^|[, ])ollama($|[, ])")
    if ($ollamaRequested) {
        $ollamaBase = Get-EnvText $EnvMap "OLLAMA_BASE_URL" "http://host.docker.internal:11434"
        $ollamaProbe = Test-HttpReachable -Url ($ollamaBase.TrimEnd("/") + "/api/tags")
        if ($ollamaProbe.ok) {
            Add-Result "Optional providers" "Ollama" "PASS" "Configured Ollama endpoint responded. No model pull was attempted." "" $ProviderGroups.ollama
        } else {
            Add-Result "Optional providers" "Ollama" "WARN" "Local AI requested, but Ollama did not respond. No model pull was attempted." "Start Ollama and provision models only when local AI is needed." $ProviderGroups.ollama
        }
    } else {
        Add-Result "Optional providers" "Ollama" "WARN" "Local AI disabled." "" $ProviderGroups.ollama
    }

    $libreBase = Get-EnvText $EnvMap "LIBRETRANSLATE_BASE_URL" ""
    if ([string]::IsNullOrWhiteSpace($libreBase)) {
        Add-Result "Optional providers" "LibreTranslate" "WARN" "Translation disabled unless profile configured." "" $ProviderGroups.libretranslate
    } else {
        Add-Result "Optional providers" "LibreTranslate" "WARN" "LibreTranslate base URL is configured, but the translation profile/service was not started by this doctor." "Use the translation profile only when translation is needed." $ProviderGroups.libretranslate
    }

    $avatarEnabled = Test-EnvTruthy -EnvMap $EnvMap -Name "ENABLE_AVATAR" -Default $false
    if ($avatarEnabled -and $WorkerImageExists -and $MuseTalkModelsPresent) {
        Add-Result "Optional providers" "Avatar rendering" "WARN" "Avatar env/image/model readiness looks present, but import smoke was not run because the doctor is read-only." "Run avatar smoke separately only when approved." $ProviderGroups.avatar
    } else {
        Add-Result "Optional providers" "Avatar rendering" "WARN" "Avatar rendering disabled until avatar profile/image/models are ready." "" $ProviderGroups.avatar
    }

    $storageBackend = (Get-EnvText $EnvMap "STORAGE_BACKEND" "filesystem").Trim().ToLowerInvariant()
    if ($storageBackend -eq "s3") {
        Add-EnvPresenceResult "Optional providers" "S3 storage adapter" $EnvMap @("S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY") "WARN" "WARN" "STORAGE_BACKEND=s3 requires S3 credentials."
    } else {
        Add-Result "Optional providers" "S3 storage adapter" "PASS" "Filesystem storage is active; S3 credentials are not required for core startup." "" $ProviderGroups.s3
    }

    $drmEnabled = Test-EnvTruthy -EnvMap $EnvMap -Name "DRM_ENABLED" -Default $false
    if ($drmEnabled) {
        Add-EnvPresenceResult "Optional providers" "DRM provider" $EnvMap @("DRM_LICENSE_URL") "WARN" "WARN" "DRM is enabled but provider metadata may be incomplete."
    } else {
        Add-Result "Optional providers" "DRM provider" "PASS" "DRM is disabled; DRM provider credentials are not required for core startup." "" $ProviderGroups.drm
    }

    $paymentNames = @("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "PAYMENT_PROVIDER", "EZDRM_API_KEY")
    $paymentPresence = Get-EnvPresence -EnvMap $EnvMap -Names $paymentNames
    if ($paymentPresence.present.Count -gt 0) {
        Add-Result "Optional providers" "payment/DRM commercial providers" "WARN" "Payment/EZDRM-like variables are present, but no runtime payment path was validated. Values were not printed." "" $paymentPresence.present
    } else {
        Add-Result "Optional providers" "payment/DRM commercial providers" "PASS" "No Stripe/payment/EZDRM env vars were detected in the local env file."
    }
}

Set-Location $RepoRoot

Add-Result "Safety" "default behavior" "PASS" "Read-only: no builds, pulls, installs, service starts, model downloads, volume/image deletes, or avatar jobs."
Add-Result "Safety" "secret handling" "PASS" "Secret-like values are reported by variable name only. Values are never printed."
Add-Result "Runtime profile" "selected profile" "PASS" $Profile

$isWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
if ($isWindows) {
    Add-Result "Host prerequisites" "Windows OS" "PASS" ([System.Environment]::OSVersion.VersionString)
} else {
    Add-Result "Host prerequisites" "Windows OS" "FAIL" ([System.Environment]::OSVersion.VersionString) "Run this Windows doctor from Windows."
}

$psVersion = $PSVersionTable.PSVersion.ToString()
if ($PSVersionTable.PSVersion.Major -ge 5) {
    Add-Result "Host prerequisites" "PowerShell" "PASS" $psVersion
} else {
    Add-Result "Host prerequisites" "PowerShell" "FAIL" $psVersion "Use Windows PowerShell 5.1+ or PowerShell 7+."
}

if (Test-CommandAvailable "git") {
    $gitVersion = Invoke-External "git" @("--version")
    if ($gitVersion.exit_code -eq 0) {
        Add-Result "Host prerequisites" "Git" "PASS" (Format-OutputLine $gitVersion)
    } else {
        Add-Result "Host prerequisites" "Git" "FAIL" (Format-OutputLine $gitVersion) "Repair Git for Windows."
    }
} else {
    Add-Result "Host prerequisites" "Git" "FAIL" "git was not found." "Install Git for Windows and reopen PowerShell."
}

if (Test-CommandAvailable "wsl.exe") {
    $wslStatus = Invoke-External "wsl.exe" @("--status")
    if ($wslStatus.exit_code -eq 0) {
        Add-Result "Host prerequisites" "WSL2" "PASS" (Format-OutputLine $wslStatus)
    } else {
        Add-Result "Host prerequisites" "WSL2" "WARN" (Format-OutputLine $wslStatus) "Check WSL2 installation and Docker Desktop WSL integration."
    }
} else {
    Add-Result "Host prerequisites" "WSL2" "FAIL" "wsl.exe was not found." "Install/enable WSL2 for the Windows Docker Desktop path."
}

if (Test-CommandAvailable "node") {
    $nodeVersion = Invoke-External "node" @("--version")
    Add-Result "Host prerequisites" "Node.js" "PASS" (Format-OutputLine $nodeVersion)
} else {
    Add-Result "Host prerequisites" "Node.js" "WARN" "node was not found." "Install Node.js 20+ for host-side frontend development/tests."
}

if (Test-CommandAvailable "npm") {
    $npmVersion = Invoke-External "npm" @("--version")
    Add-Result "Host prerequisites" "npm" "PASS" (Format-OutputLine $npmVersion)
} else {
    Add-Result "Host prerequisites" "npm" "WARN" "npm was not found." "Install Node.js 20+ for host-side frontend development/tests."
}

if (Test-Path $VenvPython) {
    $venvVersion = Invoke-External $VenvPython @("--version")
    Add-Result "Host prerequisites" "Python/.venv" "PASS" (Format-OutputLine $venvVersion)
} elseif (Test-CommandAvailable "python") {
    $pythonVersion = Invoke-External "python" @("--version")
    Add-Result "Host prerequisites" "Python/.venv" "PASS" (Format-OutputLine $pythonVersion)
} else {
    Add-Result "Host prerequisites" "Python/.venv" "WARN" "No repo .venv or python command was found." "Install Python 3.10+ or create .venv for local tools/tests."
}

if (Test-CommandAvailable "nvidia-smi") {
    $gpu = Invoke-External "nvidia-smi" @("--query-gpu=name,driver_version", "--format=csv,noheader")
    if ($gpu.exit_code -eq 0) {
        Add-Result "Host prerequisites" "NVIDIA GPU optional" "PASS" (Format-OutputLine $gpu)
    } else {
        Add-Result "Host prerequisites" "NVIDIA GPU optional" "WARN" (Format-OutputLine $gpu) "Check NVIDIA drivers before avatar GPU mode."
    }
} else {
    Add-Result "Host prerequisites" "NVIDIA GPU optional" "WARN" "nvidia-smi was not found. Core setup can still run without GPU."
}

$freeGb = Get-DriveFreeGb $RepoRoot
if ($null -eq $freeGb) {
    Add-Result "Host prerequisites" "disk space" "WARN" "Could not read free disk space for repo drive."
} elseif ($freeGb -lt 30) {
    Add-Result "Host prerequisites" "disk space" "WARN" "$freeGb GB free. This is below the 30 GB minimum guidance."
} elseif ($freeGb -lt 60) {
    Add-Result "Host prerequisites" "disk space" "WARN" "$freeGb GB free. Core may fit; avatar/full-stack should have 60 GB+ free."
} else {
    Add-Result "Host prerequisites" "disk space" "PASS" "$freeGb GB free."
}

$ports = @(
    @{ port = 3000; name = "frontend" },
    @{ port = 8000; name = "API" },
    @{ port = 8001; name = "TTS" },
    @{ port = 5432; name = "Postgres" },
    @{ port = 6379; name = "Redis" },
    @{ port = 9000; name = "MinIO API" },
    @{ port = 9001; name = "MinIO console" },
    @{ port = 11434; name = "Ollama" },
    @{ port = 5000; name = "LibreTranslate" }
)

foreach ($item in $ports) {
    if (Test-TcpPortOpen -Port $item.port) {
        Add-Result "Host prerequisites" "$($item.port) $($item.name)" "WARN" "Port $($item.port) already accepts local TCP connections. It may be an existing service or a conflict."
    } else {
        Add-Result "Host prerequisites" "$($item.port) $($item.name)" "PASS" "Port $($item.port) did not accept a local TCP connection."
    }
}

$dockerCommandAvailable = Test-CommandAvailable "docker"
$dockerDaemonReady = $false
$defaultServices = @()
$profiledServices = @()
$workerImageExists = $false

if ($dockerCommandAvailable) {
    $dockerVersion = Invoke-External "docker" @("--version")
    if ($dockerVersion.exit_code -eq 0) {
        Add-Result "Docker/runtime" "docker command" "PASS" (Format-OutputLine $dockerVersion)
    } else {
        Add-Result "Docker/runtime" "docker command" "FAIL" (Format-OutputLine $dockerVersion) "Install or repair Docker Desktop."
    }

    $composeVersion = Invoke-External "docker" @("compose", "version")
    if ($composeVersion.exit_code -eq 0) {
        Add-Result "Docker/runtime" "docker compose" "PASS" (Format-OutputLine $composeVersion)
    } else {
        Add-Result "Docker/runtime" "docker compose" "FAIL" (Format-OutputLine $composeVersion) "Install Docker Desktop with Compose v2."
    }

    $daemon = Invoke-External "docker" @("version", "--format", "{{.Server.Version}}")
    if ($daemon.exit_code -eq 0) {
        $dockerDaemonReady = $true
        Add-Result "Docker/runtime" "Docker daemon" "PASS" "Docker daemon is reachable."
    } else {
        Add-Result "Docker/runtime" "Docker daemon" "FAIL" (Format-OutputLine $daemon) "Start Docker Desktop and wait until the daemon is ready."
    }

    $composeConfig = Invoke-External "docker" @("compose", "-f", $ComposeFile, "config", "--quiet")
    if ($composeConfig.exit_code -eq 0) {
        Add-Result "Docker/runtime" "Compose config" "PASS" "infra\docker-compose.yml parsed successfully."
    } else {
        Add-Result "Docker/runtime" "Compose config" "FAIL" (Format-OutputLine $composeConfig) "Fix Docker Compose configuration or required env-file values."
    }

    $defaultComposeServices = Invoke-External "docker" @("compose", "-f", $ComposeFile, "config", "--services")
    if ($defaultComposeServices.exit_code -eq 0) {
        $defaultServices = @($defaultComposeServices.output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        Add-Result "Docker/runtime" "default Compose service list" "PASS" ($defaultServices -join ", ")
    } else {
        Add-Result "Docker/runtime" "default Compose service list" "WARN" (Format-OutputLine $defaultComposeServices) "Could not read default Compose services."
    }

    $allComposeServices = Invoke-External "docker" @("compose", "-f", $ComposeFile, "--profile", "avatar", "--profile", "translation", "config", "--services")
    if ($allComposeServices.exit_code -eq 0) {
        $profiledServices = @($allComposeServices.output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        Add-Result "Docker/runtime" "profiled Compose service list" "PASS" ($profiledServices -join ", ")
    } else {
        Add-Result "Docker/runtime" "profiled Compose service list" "WARN" (Format-OutputLine $allComposeServices) "Could not read avatar/translation profiled services."
    }

    if ($defaultServices.Count -gt 0 -or $profiledServices.Count -gt 0) {
        Add-ServiceShapeResults -DefaultServices $defaultServices -ProfiledServices $profiledServices
    } else {
        Add-Result "Docker/runtime" "service shape" "WARN" "Compose service shape could not be verified."
    }

    if ($dockerDaemonReady) {
        $imageInspect = Invoke-External "docker" @("image", "inspect", $WorkerImageName, "--format", "{{.Id}}")
        if ($imageInspect.exit_code -eq 0) {
            $workerImageExists = $true
            Add-Result "Docker/runtime" "worker image" "PASS" "$WorkerImageName exists locally. Image ID was not printed."
            $history = Invoke-External "docker" @("history", "--no-trunc", $WorkerImageName)
            if ($history.exit_code -eq 0) {
                $historyText = ($history.output | Where-Object { $_ }) -join " "
                if ($historyText -match "INSTALL_OPENMMLAB_DEPS=0|DOWNLOAD_LIVEPORTRAIT_WEIGHTS=0|Skipping OpenMMLab/mmcv|Skipping LivePortrait pretrained weights") {
                    Add-Result "Docker/runtime" "avatar image heavy deps" "WARN" "Image history contains smoke/light markers such as INSTALL_OPENMMLAB_DEPS=0 or DOWNLOAD_LIVEPORTRAIT_WEIGHTS=0."
                } elseif ($historyText -match "INSTALL_OPENMMLAB_DEPS=1|DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1") {
                    Add-Result "Docker/runtime" "avatar image heavy deps" "PASS" "Image history does not show known smoke/light skip markers."
                } else {
                    Add-Result "Docker/runtime" "avatar image heavy deps" "WARN" "Image history did not prove whether OpenMMLab and LivePortrait weights were included."
                }
            } else {
                Add-Result "Docker/runtime" "avatar image heavy deps" "WARN" "Could not inspect image history. Output was not expanded."
            }
            Add-Result "Docker/runtime" "avatar import readiness" "WARN" "OpenMMLab imports were not run because the doctor does not start containers or run avatar jobs."
        } else {
            Add-Result "Docker/runtime" "worker image" "WARN" "$WorkerImageName was not found locally." "Build or pull/tag an image later only when explicitly intended."
            Add-Result "Docker/runtime" "avatar import readiness" "WARN" "OpenMMLab imports were not run because the worker image is missing or unavailable."
        }

        $dockerInfo = Invoke-External "docker" @("info", "--format", "{{json .Runtimes}}")
        if ($dockerInfo.exit_code -eq 0 -and ((Format-OutputLine $dockerInfo) -match "nvidia")) {
            Add-Result "Docker/runtime" "Docker GPU runtime optional" "PASS" "docker info lists an nvidia runtime."
        } else {
            Add-Result "Docker/runtime" "Docker GPU runtime optional" "WARN" "docker info did not prove an nvidia runtime. No GPU container was run."
        }
    } else {
        Add-Result "Docker/runtime" "worker image" "WARN" "Docker daemon is unavailable, so local image presence could not be checked."
        Add-Result "Docker/runtime" "avatar import readiness" "WARN" "OpenMMLab imports were not run because Docker daemon is unavailable."
    }
} else {
    Add-Result "Docker/runtime" "docker command" "FAIL" "docker was not found." "Install Docker Desktop and enable WSL2 integration."
    Add-Result "Docker/runtime" "docker compose" "FAIL" "docker compose could not be checked because docker is missing." "Install Docker Desktop with Compose v2."
    Add-Result "Docker/runtime" "Docker daemon" "FAIL" "Docker daemon could not be checked because docker is missing." "Install/start Docker Desktop."
    Add-Result "Docker/runtime" "Compose config" "FAIL" "Compose config could not be checked because docker is missing."
}

if (Test-Path $EnvExampleFile) {
    Add-Result "Env files" "infra\.env.example" "PASS" "Template exists."
} else {
    Add-Result "Env files" "infra\.env.example" "FAIL" "Template is missing." "Restore infra\.env.example."
}

$envMap = Read-EnvFile $EnvFile
if (Test-Path $EnvFile) {
    Add-Result "Env files" "infra\.env" "PASS" "Local env file exists. Values were not printed."
    Add-EnvPresenceResult "Env files" "required local core variables" $envMap $RequiredCoreEnv "WARN" "WARN" "Copy infra\.env.example to infra\.env and fill local values."
    Add-EnvPresenceResult "Env files" "recommended local variables" $envMap $RecommendedCoreEnv "WARN" "WARN" "Review docs\ENVIRONMENT_VARIABLES.md."

    $debugFalse = -not (Test-EnvTruthy -EnvMap $envMap -Name "DEBUG" -Default $true)
    if ($debugFalse) {
        Add-EnvPresenceResult "Env files" "production required minimum" $envMap $ProductionRequiredEnv "FAIL" "FAIL" "DEBUG=False requires production-safe values."
        if (Test-EnvTruthy -EnvMap $envMap -Name "CORS_ALLOW_ALL_ORIGINS" -Default $false) {
            Add-Result "Env files" "production CORS" "FAIL" "CORS_ALLOW_ALL_ORIGINS is true while DEBUG=False. Value was not printed." "Set explicit CORS_ALLOWED_ORIGINS."
        } else {
            Add-Result "Env files" "production CORS" "PASS" "CORS_ALLOW_ALL_ORIGINS is not enabled for DEBUG=False."
        }
    } else {
        Add-Result "Env files" "production required minimum" "PASS" "DEBUG is not false; production-only required minimum is documented but not enforced for local readiness." "" $ProductionRequiredEnv
    }
} else {
    Add-Result "Env files" "infra\.env" "FAIL" "Local env file is missing." "Create it with: Copy-Item infra\.env.example infra\.env"
    Add-Result "Env files" "required local core variables" "FAIL" "Cannot check required local variables because infra\.env is missing." "Create infra\.env from the template." $RequiredCoreEnv
}

Add-AvatarModelChecks
$museTalkPresence = @($Results | Where-Object { $_.category -eq "Models/assets" -and $_.name -eq "MuseTalk model bundle" -and $_.status -eq "PASS" })
$museTalkModelsPresent = [bool]$museTalkPresence
Add-OptionalProviderChecks -EnvMap $envMap -DockerDaemonReady:$dockerDaemonReady -WorkerImageExists:$workerImageExists -MuseTalkModelsPresent:$museTalkModelsPresent

$summary = @(
    [pscustomobject]@{ name = "Safety contract"; status = Get-ResultStatus @("Safety") },
    [pscustomobject]@{ name = "Host prerequisites"; status = Get-ResultStatus @("Host prerequisites") },
    [pscustomobject]@{ name = "Docker/runtime"; status = Get-ResultStatus @("Docker/runtime") },
    [pscustomobject]@{ name = "Env files"; status = Get-ResultStatus @("Env files") },
    [pscustomobject]@{ name = "Optional providers"; status = Get-ResultStatus @("Optional providers") },
    [pscustomobject]@{ name = "Models/assets"; status = Get-ResultStatus @("Models/assets") }
)

$hasFailure = [bool]($Results | Where-Object { $_.status -eq "FAIL" })
$exitCode = if ($hasFailure) { 1 } else { 0 }

$payload = [pscustomobject]@{
    generated_at = (Get-Date).ToString("o")
    repo_root = $RepoRoot
    selected_profile = $Profile
    safety = [pscustomobject]@{
        read_only_default = $true
        no_builds = $true
        no_pulls = $true
        no_installs = $true
        no_service_start = $true
        no_model_downloads = $true
        no_volume_or_image_delete = $true
        no_avatar_jobs = $true
        secret_values_printed = $false
    }
    required_core_env = @($RequiredCoreEnv)
    recommended_core_env = @($RecommendedCoreEnv)
    production_required_env = @($ProductionRequiredEnv)
    optional_provider_groups = $ProviderGroups
    results = @($Results.ToArray())
    summary = @($summary)
    exit_code = $exitCode
}

$jsonText = $payload | ConvertTo-Json -Depth 8
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $targetPath = $OutputPath
    if (-not [System.IO.Path]::IsPathRooted($targetPath)) {
        $targetPath = Join-Path $RepoRoot $targetPath
    }
    $parent = Split-Path -Parent $targetPath
    if (-not [string]::IsNullOrWhiteSpace($parent) -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Set-Content -Path $targetPath -Value $jsonText -Encoding UTF8
}

if ($Json) {
    $jsonText
    exit $exitCode
}

Write-Host "VISUS VidLab Windows doctor"
Write-Host "Repo root: $RepoRoot"
Write-Host "Profile: $Profile"
Write-Host "Read-only: no builds, pulls, installs, service starts, model downloads, volume/image deletes, or avatar jobs."
Write-Host "Secret values are never printed."
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    Write-Host "JSON report written: $OutputPath"
}
Write-Host ""

foreach ($group in ($Results | Group-Object category)) {
    Write-Host "== $($group.Name) =="
    foreach ($result in $group.Group) {
        Write-Host ("[{0}] {1}: {2}" -f $result.status, $result.name, $result.detail)
        if ($result.variables -and $result.variables.Count -gt 0) {
            Write-Host ("      Variables: {0}" -f (($result.variables | Sort-Object -Unique) -join ", "))
        }
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
    Write-Host "FAIL items were found. Resolve core blockers before treating this checkout as installer-ready."
}

exit $exitCode
