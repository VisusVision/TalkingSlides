[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    [ValidateSet("staging", "secure_stream", "drm_protected")]
    [string]$Profile = "secure_stream"
)

$ErrorActionPreference = "Stop"
$Critical = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]

function Add-Critical {
    param([string]$Message)
    $Critical.Add($Message) | Out-Null
}

function Add-Warning {
    param([string]$Message)
    $Warnings.Add($Message) | Out-Null
}

function Convert-EnvBool {
    param([string]$Value)
    $normalized = ([string]$Value).Trim().ToLowerInvariant()
    return $normalized -in @("1", "true", "yes", "on")
}

function Get-EnvValue {
    param(
        [hashtable]$Env,
        [string]$Name
    )
    if ($Env.ContainsKey($Name)) {
        return [string]$Env[$Name]
    }
    return ""
}

function Test-SecretLooksUnsafe {
    param([string]$Value)
    $normalized = ([string]$Value).Trim().ToLowerInvariant()
    if (-not $normalized) {
        return $true
    }
    $unsafePatterns = @(
        "dev-insecure-secret-key-change-me",
        "media-token-dev-secret-change-in-prod",
        "replace-with",
        "placeholder",
        "change-me",
        "local-dev",
        "example-secret"
    )
    foreach ($pattern in $unsafePatterns) {
        if ($normalized.Contains($pattern)) {
            return $true
        }
    }
    return $false
}

function Require-Key {
    param(
        [hashtable]$Env,
        [string]$Name,
        [string]$Reason
    )
    $value = Get-EnvValue -Env $Env -Name $Name
    if ([string]::IsNullOrWhiteSpace($value)) {
        Add-Critical "$Name is required. $Reason"
    }
}

if (-not (Test-Path $EnvFile)) {
    Write-Error "Env file not found: $EnvFile"
    exit 1
}

$resolvedEnvFile = Resolve-Path $EnvFile
$envMap = @{}
$lineNumber = 0
foreach ($line in Get-Content $resolvedEnvFile) {
    $lineNumber += 1
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
        continue
    }
    if ($trimmed.StartsWith("export ")) {
        $trimmed = $trimmed.Substring(7).Trim()
    }
    $match = [regex]::Match($trimmed, '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$')
    if (-not $match.Success) {
        Add-Warning "Line $lineNumber could not be parsed as KEY=value."
        continue
    }
    $key = $match.Groups[1].Value
    $value = $match.Groups[2].Value.Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    $envMap[$key] = $value
}

Write-Host "Checking deployment env file: $resolvedEnvFile"
Write-Host "Profile: $Profile"
Write-Host "Secret values are not printed."

$commonRequired = @(
    "DEBUG",
    "SECRET_KEY",
    "POSTGRES_HOST",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "MEDIA_TOKEN_SECRET",
    "ALLOWED_HOSTS",
    "CSRF_TRUSTED_ORIGINS",
    "CORS_ALLOWED_ORIGINS"
)

foreach ($name in $commonRequired) {
    Require-Key -Env $envMap -Name $name -Reason "Required by production fail-fast configuration."
}

$debugValue = Get-EnvValue -Env $envMap -Name "DEBUG"
if (Convert-EnvBool $debugValue) {
    Add-Warning "DEBUG is true. Production-like profiles should use DEBUG=False."
}

$allowedHosts = Get-EnvValue -Env $envMap -Name "ALLOWED_HOSTS"
if ($allowedHosts.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ -eq "*" }) {
    Add-Warning "ALLOWED_HOSTS contains wildcard '*'. Avoid this in production."
}

$corsAllowAll = Get-EnvValue -Env $envMap -Name "CORS_ALLOW_ALL_ORIGINS"
if (Convert-EnvBool $corsAllowAll) {
    Add-Warning "CORS_ALLOW_ALL_ORIGINS is true. Production fail-fast settings reject this when DEBUG=False."
}

if (Test-SecretLooksUnsafe (Get-EnvValue -Env $envMap -Name "SECRET_KEY")) {
    Add-Warning "SECRET_KEY looks like a development/default/placeholder value."
}

if (Test-SecretLooksUnsafe (Get-EnvValue -Env $envMap -Name "MEDIA_TOKEN_SECRET")) {
    Add-Warning "MEDIA_TOKEN_SECRET looks like a development/default/placeholder value."
}

if ([string]::IsNullOrWhiteSpace((Get-EnvValue -Env $envMap -Name "POSTGRES_HOST"))) {
    Add-Warning "POSTGRES_HOST is missing. Django would fall back to SQLite outside production fail-fast mode."
}

if ($Profile -in @("staging", "secure_stream", "drm_protected")) {
    Require-Key -Env $envMap -Name "REDIS_URL" -Reason "Redis is required for Celery/cache in staging and production."
    Require-Key -Env $envMap -Name "STORAGE_ROOT" -Reason "Shared durable media storage must be configured."
    Require-Key -Env $envMap -Name "TTS_SERVICE_URL" -Reason "Render workers need the TTS service."
    $storageRoot = Get-EnvValue -Env $envMap -Name "STORAGE_ROOT"
    if (-not [string]::IsNullOrWhiteSpace($storageRoot) -and -not [System.IO.Path]::IsPathRooted($storageRoot)) {
        Add-Warning "STORAGE_ROOT should be an absolute path. Django rejects relative STORAGE_ROOT when DEBUG=False."
    }
}

if ($Profile -in @("staging", "secure_stream")) {
    $mode = (Get-EnvValue -Env $envMap -Name "LESSON_PROTECTION_DEFAULT_MODE").Trim()
    if (-not $mode) {
        Add-Critical "LESSON_PROTECTION_DEFAULT_MODE is required for secure_stream-style profiles."
    } elseif ($mode -ne "secure_stream") {
        Add-Warning "LESSON_PROTECTION_DEFAULT_MODE is not secure_stream for profile $Profile."
    }
    if (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "DRM_ENABLED")) {
        Add-Warning "DRM_ENABLED is true in a non-DRM profile. Keep DRM disabled unless provider config is ready."
    }
}

if ($Profile -eq "secure_stream") {
    Require-Key -Env $envMap -Name "DRM_STREAMING_ENABLED" -Reason "secure_stream production should explicitly set HLS/streaming behavior."
    if (-not (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "DRM_STREAMING_ENABLED"))) {
        Add-Warning "DRM_STREAMING_ENABLED is not true. HLS packaging may be disabled for secure_stream."
    }
    if (-not (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "LECTURE_WATERMARK_ENABLED"))) {
        Add-Warning "LECTURE_WATERMARK_ENABLED is not true."
    }
    if (-not (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "VITE_PLAYER_HEARTBEAT_ENABLED"))) {
        Add-Warning "VITE_PLAYER_HEARTBEAT_ENABLED is not true."
    }
}

if ($Profile -eq "drm_protected") {
    $mode = (Get-EnvValue -Env $envMap -Name "LESSON_PROTECTION_DEFAULT_MODE").Trim()
    if ($mode -ne "drm_protected") {
        Add-Critical "LESSON_PROTECTION_DEFAULT_MODE must be drm_protected for the drm_protected profile."
    }
    if (-not (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "DRM_ENABLED"))) {
        Add-Critical "DRM_ENABLED must be true for the drm_protected profile."
    }
    if (-not (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "DRM_STREAMING_ENABLED"))) {
        Add-Critical "DRM_STREAMING_ENABLED must be true for the drm_protected profile."
    }
    if (-not (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "DRM_HLS_ENCRYPTION_ENABLED"))) {
        Add-Critical "DRM_HLS_ENCRYPTION_ENABLED must be true for the drm_protected profile."
    }
    if (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "LESSON_PROTECTION_ALLOW_MP4_FALLBACK")) {
        Add-Warning "LESSON_PROTECTION_ALLOW_MP4_FALLBACK is true. DRM-protected production should not allow MP4 fallback."
    }
    if (-not (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "VITE_PLAYER_ENABLE_DRM_SHAKA"))) {
        Add-Warning "VITE_PLAYER_ENABLE_DRM_SHAKA is not true for the drm_protected profile."
    }

    $enabledDrmSystems = @()
    if (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "DRM_WIDEVINE_ENABLED")) {
        $enabledDrmSystems += "WIDEVINE"
    }
    if (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "DRM_PLAYREADY_ENABLED")) {
        $enabledDrmSystems += "PLAYREADY"
    }
    if (Convert-EnvBool (Get-EnvValue -Env $envMap -Name "DRM_FAIRPLAY_ENABLED")) {
        $enabledDrmSystems += "FAIRPLAY"
    }
    if ($enabledDrmSystems.Count -eq 0) {
        Add-Critical "At least one DRM system must be enabled for drm_protected."
    }
    foreach ($system in $enabledDrmSystems) {
        Require-Key -Env $envMap -Name "DRM_${system}_LICENSE_URL" -Reason "$system license URL is required when that DRM system is enabled."
        if ($system -eq "FAIRPLAY") {
            Require-Key -Env $envMap -Name "DRM_FAIRPLAY_CERTIFICATE_URL" -Reason "FairPlay requires a certificate URL."
        }
    }

    $provider = (Get-EnvValue -Env $envMap -Name "DRM_PROVIDER_NAME").Trim().ToLowerInvariant()
    $keySystem = (Get-EnvValue -Env $envMap -Name "DRM_KEY_SYSTEM").Trim().ToLowerInvariant()
    if ($provider.Contains("clearkey") -or $keySystem.Contains("clearkey")) {
        Add-Warning "Clear Key-like DRM configuration detected. Do not use Clear Key as a production shortcut."
    }
}

Write-Host ""
if ($Warnings.Count -gt 0) {
    Write-Host "Warnings:"
    foreach ($warning in $Warnings) {
        Write-Host "- $warning"
    }
} else {
    Write-Host "Warnings: none"
}

if ($Critical.Count -gt 0) {
    Write-Host ""
    Write-Host "Critical issues:"
    foreach ($issue in $Critical) {
        Write-Host "- $issue"
    }
    exit 1
}

Write-Host ""
Write-Host "Critical checks passed."
exit 0
