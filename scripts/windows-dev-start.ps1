[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$WithTts,
    [switch]$WithWorker,
    [switch]$WithAvatar
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ComposeFile = Join-Path $RepoRoot "infra\docker-compose.yml"
$EnvFile = Join-Path $RepoRoot "infra\.env"

function Test-CommandAvailable {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Set-Location $RepoRoot

if (-not (Test-CommandAvailable "docker")) {
    throw "Docker was not found. Install/start Docker Desktop first."
}

if (-not (Test-Path $EnvFile)) {
    Write-Warning "Missing infra\.env. Create it with: Copy-Item infra\.env.example infra\.env"
}

if ($WithAvatar) {
    $WithTts = $true
    $WithWorker = $true
}

if ($WithWorker) {
    $WithTts = $true
}

$services = New-Object System.Collections.Generic.List[string]
foreach ($service in @("postgres", "redis", "minio", "api", "frontend")) {
    $services.Add($service) | Out-Null
}
if ($WithTts) {
    $services.Add("tts_service") | Out-Null
}
if ($WithWorker) {
    $services.Add("worker") | Out-Null
}
if ($WithAvatar) {
    $services.Add("worker-avatar") | Out-Null
}

$composeArgs = @("compose", "-f", $ComposeFile, "up", "-d")
if ($Build) {
    $composeArgs += "--build"
}
$composeArgs += $services.ToArray()

Write-Host "Starting services:"
foreach ($service in $services) {
    Write-Host "- $service"
}

& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Service URLs:"
Write-Host "- Frontend:      http://localhost:3000"
Write-Host "- API:           http://localhost:8000"
Write-Host "- API readiness: http://localhost:8000/api/v1/ready/"
if ($WithTts) {
    Write-Host "- TTS:           http://localhost:8001"
}
Write-Host "- MinIO console: http://localhost:9001"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  docker compose -f infra\docker-compose.yml ps"
Write-Host "  docker compose -f infra\docker-compose.yml logs -f api"
Write-Host "  docker compose -f infra\docker-compose.yml logs -f frontend"
if ($WithWorker) {
    Write-Host "  docker compose -f infra\docker-compose.yml logs -f worker"
}
if ($WithAvatar) {
    Write-Host "  docker compose -f infra\docker-compose.yml logs -f worker-avatar"
}
