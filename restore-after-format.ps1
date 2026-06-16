<#
Restore Docker volumes and optionally restore VS Code, SSH, and Git config files
after reinstalling Windows.

Copy the saved C:\docker-backup folder back to the new system before running.
#>

[CmdletBinding()]
param(
    [string]$BackupRoot = "C:\docker-backup",
    [string]$DockerImage = "alpine:3.20",
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Warning $Message
}

function Confirm-Action {
    param([Parameter(Mandatory = $true)][string]$Message)

    if ($Force) {
        return $true
    }

    $answer = Read-Host "$Message [y/N]"
    return ($answer -match '^(y|yes|e|evet)$')
}

function Assert-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker komutu bulunamadi. Docker Desktop kurulu mu ve PATH icinde mi?"
    }

    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker calismiyor veya Docker Desktop hazir degil. Docker Desktop'i acip tekrar deneyin."
    }
}

function Get-VolumeBackups {
    param([Parameter(Mandatory = $true)][string]$Root)

    $volumesDir = Join-Path $Root "volumes"
    $manifestPath = Join-Path $Root "backup-manifest.json"
    $items = @()

    if (Test-Path -LiteralPath $manifestPath) {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        foreach ($volume in @($manifest.volumes)) {
            $archivePath = Join-Path $volumesDir $volume.archive
            if (Test-Path -LiteralPath $archivePath) {
                $items += [pscustomobject]@{
                    Name = [string]$volume.name
                    Archive = [string]$volume.archive
                    ArchivePath = $archivePath
                }
            }
            else {
                Write-Warn "Manifestte var ama arsiv bulunamadi: $archivePath"
            }
        }
    }
    else {
        Write-Warn "backup-manifest.json bulunamadi. Volume adlari .tar.gz dosya adindan tahmin edilecek."
        if (Test-Path -LiteralPath $volumesDir) {
            Get-ChildItem -LiteralPath $volumesDir -Filter "*.tar.gz" -File | ForEach-Object {
                $name = $_.Name -replace '\.tar\.gz$', ''
                $items += [pscustomobject]@{
                    Name = $name
                    Archive = $_.Name
                    ArchivePath = $_.FullName
                }
            }
        }
    }

    return $items
}

function Restore-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Warn "$Label yedegi bulunamadi, atlandi: $Source"
        return
    }

    if (-not (Confirm-Action "$Label geri yuklensin mi?")) {
        Write-Warn "$Label geri yukleme atlandi."
        return
    }

    if (Test-Path -LiteralPath $Destination) {
        if (-not (Confirm-Action "Hedef klasor mevcut, dosyalarin ustune yazilabilir: $Destination. Devam edilsin mi?")) {
            Write-Warn "$Label geri yukleme atlandi."
            return
        }
    }

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null

    Get-ChildItem -LiteralPath $Source -Recurse -Force | ForEach-Object {
        $relative = $_.FullName.Substring($Source.Length).TrimStart('\')
        $target = Join-Path $Destination $relative

        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Path $target -Force | Out-Null
        }
        else {
            $targetParent = Split-Path -Parent $target
            New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }

    Write-Host "$Label geri yuklendi: $Destination" -ForegroundColor Green
}

function Restore-FileWithPrompt {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Warn "$Label yedegi bulunamadi, atlandi: $Source"
        return
    }

    if (Test-Path -LiteralPath $Destination) {
        if (-not (Confirm-Action "$Destination zaten var. Ustune yazilsin mi?")) {
            Write-Warn "$Label geri yukleme atlandi."
            return
        }
    }
    elseif (-not (Confirm-Action "$Label geri yuklensin mi?")) {
        Write-Warn "$Label geri yukleme atlandi."
        return
    }

    $parent = Split-Path -Parent $Destination
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
    Write-Host "$Label geri yuklendi: $Destination" -ForegroundColor Green
}

if (-not (Test-Path -LiteralPath $BackupRoot)) {
    throw "Backup klasoru bulunamadi: $BackupRoot"
}

Write-Step "Docker durumu kontrol ediliyor"
Assert-DockerReady

Write-Step "Docker volume yedekleri bulunuyor"
$volumeBackups = @(Get-VolumeBackups -Root $BackupRoot)
if ($volumeBackups.Count -eq 0) {
    Write-Warn "C:\docker-backup icinde .tar.gz volume yedegi bulunamadi."
}
else {
    $volumesDir = Join-Path $BackupRoot "volumes"
    $existingVolumes = @(& docker volume ls --format "{{.Name}}")

    foreach ($backup in $volumeBackups) {
        $volumeName = $backup.Name
        $archiveName = $backup.Archive

        if ($existingVolumes -notcontains $volumeName) {
            Write-Host "Volume olusturuluyor: $volumeName"
            & docker volume create $volumeName | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "Volume olusturulamadi, atlandi: $volumeName"
                continue
            }
        }
        else {
            if (-not (Confirm-Action "Volume zaten var: $volumeName. Arsiv iceriği volume icine acilsin mi?")) {
                Write-Warn "Volume geri yukleme atlandi: $volumeName"
                continue
            }
        }

        Write-Host "Geri yukleniyor: $archiveName -> $volumeName"
        & docker run --rm `
            -v "${volumeName}:/volume" `
            -v "${volumesDir}:/backup:ro" `
            $DockerImage `
            sh -c "tar -xzf '/backup/$archiveName' -C /volume"

        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Volume geri yuklenemedi: $volumeName"
            continue
        }

        Write-Host "Volume geri yuklendi: $volumeName" -ForegroundColor Green
    }
}

Write-Step "VS Code ayarlari icin secenek sunuluyor"
$vscodeSource = Join-Path $BackupRoot "vscode\User"
$vscodeDestination = Join-Path $env:APPDATA "Code\User"
Restore-DirectoryContents -Source $vscodeSource -Destination $vscodeDestination -Label "VS Code User klasoru"

Write-Step "SSH anahtarlari icin secenek sunuluyor"
$sshSource = Join-Path $BackupRoot "user-files\.ssh"
$sshDestination = Join-Path $HOME ".ssh"
Restore-DirectoryContents -Source $sshSource -Destination $sshDestination -Label "SSH klasoru"

Write-Step "Git config dosyalari icin secenek sunuluyor"
Restore-FileWithPrompt -Source (Join-Path $BackupRoot "user-files\.gitconfig") -Destination (Join-Path $HOME ".gitconfig") -Label ".gitconfig"
Restore-FileWithPrompt -Source (Join-Path $BackupRoot "user-files\.git-credentials") -Destination (Join-Path $HOME ".git-credentials") -Label ".git-credentials"

$projectFiles = Join-Path $BackupRoot "project-files"
if (Test-Path -LiteralPath $projectFiles) {
    Write-Host ""
    Write-Host "Compose/env dosyalarinin yedegi burada: $projectFiles"
    Write-Host "Proje klasorlerine ihtiyaca gore elle kopyalayin."
}

Write-Host ""
Write-Host "Restore tamamlandi. Ilgili proje klasorlerinde docker compose up -d calistirin." -ForegroundColor Green
