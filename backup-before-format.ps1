<#
Backup Docker volumes, VS Code settings, SSH keys, Git config, and compose/env files
before formatting Windows.

Run this script from the project folder whose compose/env files you want to collect.
#>

[CmdletBinding()]
param(
    [string]$BackupRoot = "C:\docker-backup",
    [string]$SourceRoot = (Get-Location).Path,
    [string]$DockerImage = "alpine:3.20"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DockerVolumes = @(
    "6f74e1f677fe8b5d34e2de33ade1ce7d2e2eb65268dc6f9ccc6cd4dbf92d0096",
    "8fe4c89ee3172af5036d88b5ff2afd77c046339ecee9c8370540d94d55e4b210",
    "99276b4ea931aa2b1960c09f33e0e866808ef453d5f0a2e438329d7d1810732b",
    "ai-video-generator_jobs_data",
    "ai-video-generator_outputs_data",
    "ai-video-generator_temp_data",
    "ai-video-generator_uploads_data",
    "backend_backend_uploads",
    "backend_freelance_mysql_data",
    "backend_mysql_data",
    "freelance-platform-backend_backend_uploads",
    "freelance-platform-backend_freelance_mysql_data",
    "freelance_freelance_mysql_data",
    "infra_grafana_data",
    "infra_minio_data",
    "infra_postgres_data",
    "infra_redis_data",
    "proje_backend_uploads",
    "proje_freelance_mysql_data"
)

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Warning $Message
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

function ConvertTo-SafeFileName {
    param([Parameter(Mandatory = $true)][string]$Name)

    $safe = $Name -replace '[^A-Za-z0-9._-]', '_'
    $safe = $safe.Trim(". ")
    if ([string]::IsNullOrWhiteSpace($safe)) {
        $safe = "volume"
    }
    if ($safe.Length -gt 120) {
        $safe = $safe.Substring(0, 120)
    }
    return $safe
}

function Reset-DirectoryInsideBackup {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $pathFull = [System.IO.Path]::GetFullPath($Path)

    if (-not $pathFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Guvenlik nedeniyle BackupRoot disinda klasor silinmedi: $pathFull"
    }

    if (Test-Path -LiteralPath $pathFull) {
        Remove-Item -LiteralPath $pathFull -Recurse -Force
    }
    New-Item -ItemType Directory -Path $pathFull -Force | Out-Null
}

function Copy-DirectoryFresh {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$BackupRoot
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Warn "Kaynak bulunamadi, atlandi: $Source"
        return $false
    }

    Reset-DirectoryInsideBackup -Path $Destination -Root $BackupRoot
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
    return $true
}

function Get-RelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$FullPath
    )

    $baseFull = [System.IO.Path]::GetFullPath($BasePath).TrimEnd('\') + '\'
    $fileFull = [System.IO.Path]::GetFullPath($FullPath)
    $baseUri = [System.Uri]::new($baseFull)
    $fileUri = [System.Uri]::new($fileFull)
    return [System.Uri]::UnescapeDataString($baseUri.MakeRelativeUri($fileUri).ToString()).Replace('/', '\')
}

function Copy-ProjectConfigFiles {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $wantedNames = @("docker-compose.yml", "compose.yml", ".env", ".env.local", ".env.production")
    Reset-DirectoryInsideBackup -Path $Destination -Root $BackupRoot

    $copied = @()
    Get-ChildItem -LiteralPath $Root -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $wantedNames -contains $_.Name } |
        ForEach-Object {
            $relative = Get-RelativePath -BasePath $Root -FullPath $_.FullName
            $target = Join-Path $Destination $relative
            $targetParent = Split-Path -Parent $target
            New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
            $copied += $relative
        }

    return $copied
}

Write-Step "Backup klasorleri hazirlaniyor"
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
$VolumesBackupDir = Join-Path $BackupRoot "volumes"
$VsCodeBackupDir = Join-Path $BackupRoot "vscode"
$UserFilesBackupDir = Join-Path $BackupRoot "user-files"
$ProjectFilesBackupDir = Join-Path $BackupRoot "project-files"
New-Item -ItemType Directory -Path $VolumesBackupDir, $VsCodeBackupDir, $UserFilesBackupDir, $ProjectFilesBackupDir -Force | Out-Null

Write-Step "Docker durumu kontrol ediliyor"
Assert-DockerReady

$manifest = [ordered]@{
    created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    backup_root = $BackupRoot
    source_root = ([System.IO.Path]::GetFullPath($SourceRoot))
    docker_image = $DockerImage
    volumes = @()
    skipped_volumes = @()
    vscode_user_backed_up = $false
    ssh_backed_up = $false
    git_files_backed_up = @()
    project_files_backed_up = @()
}

Write-Step "Docker volume yedekleri aliniyor"
$existingVolumes = @(& docker volume ls --format "{{.Name}}")
$usedArchiveNames = @{}

foreach ($volumeName in $DockerVolumes) {
    if ($existingVolumes -notcontains $volumeName) {
        Write-Warn "Docker volume bulunamadi, atlandi: $volumeName"
        $manifest.skipped_volumes += $volumeName
        continue
    }

    $safeBase = ConvertTo-SafeFileName -Name $volumeName
    $archiveName = "$safeBase.tar.gz"
    $counter = 2
    while ($usedArchiveNames.ContainsKey($archiveName)) {
        $archiveName = "$safeBase-$counter.tar.gz"
        $counter++
    }
    $usedArchiveNames[$archiveName] = $true

    $archivePath = Join-Path $VolumesBackupDir $archiveName
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }

    Write-Host "Yedekleniyor: $volumeName -> $archiveName"
    & docker run --rm `
        -v "${volumeName}:/volume:ro" `
        -v "${VolumesBackupDir}:/backup" `
        $DockerImage `
        sh -c "tar -czf '/backup/$archiveName' -C /volume ."

    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Volume yedeklenemedi, devam ediliyor: $volumeName"
        $manifest.skipped_volumes += $volumeName
        continue
    }

    $manifest.volumes += [ordered]@{
        name = $volumeName
        archive = $archiveName
    }
}

Write-Step "VS Code ayarlari yedekleniyor"
$codeUserPath = Join-Path $env:APPDATA "Code\User"
if (Copy-DirectoryFresh -Source $codeUserPath -Destination (Join-Path $VsCodeBackupDir "User") -BackupRoot $BackupRoot) {
    $manifest.vscode_user_backed_up = $true
}

Write-Step "SSH anahtarlari yedekleniyor"
$sshPath = Join-Path $HOME ".ssh"
if (Copy-DirectoryFresh -Source $sshPath -Destination (Join-Path $UserFilesBackupDir ".ssh") -BackupRoot $BackupRoot) {
    $manifest.ssh_backed_up = $true
}

Write-Step "Git config dosyalari yedekleniyor"
foreach ($gitFileName in @(".gitconfig", ".git-credentials")) {
    $source = Join-Path $HOME $gitFileName
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $UserFilesBackupDir $gitFileName) -Force
        $manifest.git_files_backed_up += $gitFileName
        Write-Host "Yedeklendi: $source"
    }
    else {
        Write-Warn "Git dosyasi bulunamadi, atlandi: $source"
    }
}

Write-Step "Compose ve env dosyalari araniyor"
$manifest.project_files_backed_up = @(Copy-ProjectConfigFiles -Root $SourceRoot -Destination $ProjectFilesBackupDir)
if ($manifest.project_files_backed_up.Count -eq 0) {
    Write-Warn "docker-compose.yml, compose.yml veya .env dosyasi bulunamadi: $SourceRoot"
}
else {
    $manifest.project_files_backed_up | ForEach-Object { Write-Host "Yedeklendi: $_" }
}

Write-Step "Manifest yaziliyor"
$manifestPath = Join-Path $BackupRoot "backup-manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $manifestPath -Encoding UTF8
Write-Host "Manifest: $manifestPath"

Write-Step "Backup icerigi"
Get-ChildItem -LiteralPath $BackupRoot -Recurse -Force |
    Select-Object FullName, Length, LastWriteTime |
    Format-Table -AutoSize

Write-Host ""
Write-Warning "Bu klasörü format atılmayacak harici diske veya buluta kopyala."
Write-Host "Backup tamamlandi: $BackupRoot" -ForegroundColor Green
