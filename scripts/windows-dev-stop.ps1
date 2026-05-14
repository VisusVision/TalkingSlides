[CmdletBinding()]
param(
    [switch]$RemoveVolumes,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ComposeFile = Join-Path $RepoRoot "infra\docker-compose.yml"

function Test-CommandAvailable {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Set-Location $RepoRoot

if (-not (Test-CommandAvailable "docker")) {
    throw "Docker was not found. Install/start Docker Desktop first."
}

$composeArgs = @("compose", "-f", $ComposeFile, "down")

if ($RemoveVolumes) {
    if (-not $Force) {
        Write-Warning "This will remove Docker Compose volumes for Postgres, Redis, and MinIO."
        Write-Warning "Local database and object-store data in those volumes will be deleted."
        $answer = Read-Host "Type REMOVE to continue"
        if ($answer -ne "REMOVE") {
            Write-Host "Cancelled. Stopping containers without removing volumes."
        } else {
            $composeArgs += "-v"
        }
    } else {
        $composeArgs += "-v"
    }
}

& docker @composeArgs
exit $LASTEXITCODE
