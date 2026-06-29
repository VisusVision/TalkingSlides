[CmdletBinding()]
param(
    [ValidateSet("Plan", "Check", "PrintBuildCommand", "PrintSmokeCommand")]
    [string]$Action = "Plan",
    [string]$LocalWheel = "local_wheels/mmcv-2.0.1-cp310-cp310-manylinux1_x86_64.whl",
    [string]$MmcWheelUrl = "https://example.internal/mmcv-2.0.1-cp310-cp310-manylinux1_x86_64.whl",
    [string]$PrebuiltImage = "registry.example.internal/ai-academy-worker-avatar:cuda118-mmcv201",
    [string]$ModelRoot = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($ModelRoot)) {
    $ModelRoot = Join-Path $RepoRoot "storage_local\models"
}

$RequiredModelFiles = @(
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

$OptionalModelPaths = @(
    "storage_local\models\liveportrait",
    "storage_local\avatar_templates\calm_lecture_driver.mp4"
)

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Write-SafetyBanner {
    Write-Host "VISUS VidLab avatar runtime helper"
    Write-Host "Repo root: $RepoRoot"
    Write-Host "Action: $Action"
    Write-Host ""
    Write-Host "This script is non-destructive. It prints plans and commands only."
    Write-Host "It does not build images, pull images, start services, install packages, download models, delete volumes, or run avatar jobs."
}

function Write-CommandBlock {
    param(
        [string]$Title,
        [string[]]$Commands
    )

    Write-Section $Title
    Write-Host "Review first, then copy/paste manually when you intend to run it later:"
    foreach ($command in $Commands) {
        Write-Host $command
    }
}

function Write-ModelBundlePaths {
    Write-Section "Model bundle paths"
    Write-Host "Required MuseTalk model root:"
    Write-Host "storage_local\models"
    Write-Host ""
    Write-Host "Required files:"
    foreach ($relativePath in $RequiredModelFiles) {
        Write-Host "- storage_local\models\$relativePath"
    }
    Write-Host ""
    Write-Host "Optional/related paths:"
    foreach ($relativePath in $OptionalModelPaths) {
        Write-Host "- $relativePath"
    }
}

function Write-BuildCommands {
    Write-CommandBlock "Online OpenMMLab build" @(
        'docker compose -f infra\docker-compose.yml --profile avatar build --progress=plain --build-arg INSTALL_AVATAR_RUNTIME_DEPS=1 --build-arg INSTALL_OPENMMLAB_DEPS=1 --build-arg DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1 worker-avatar'
    )

    Write-CommandBlock "Local wheel/cache build" @(
        'New-Item -ItemType Directory -Force local_wheels',
        "# Put a compatible mmcv wheel at $LocalWheel before running the build command.",
        'docker compose -f infra\docker-compose.yml --profile avatar build --progress=plain --build-arg INSTALL_AVATAR_RUNTIME_DEPS=1 --build-arg INSTALL_OPENMMLAB_DEPS=1 --build-arg DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1 --build-arg MMCV_LOCAL_WHEEL=' + $LocalWheel + ' worker-avatar'
    )

    Write-CommandBlock "Pinned wheel URL build" @(
        "# Replace this placeholder with an internal artifact URL that you trust: $MmcWheelUrl",
        'docker compose -f infra\docker-compose.yml --profile avatar build --progress=plain --build-arg INSTALL_AVATAR_RUNTIME_DEPS=1 --build-arg INSTALL_OPENMMLAB_DEPS=1 --build-arg DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1 --build-arg MMCV_WHEEL_URL=' + $MmcWheelUrl + ' worker-avatar'
    )

    Write-CommandBlock "Prebuilt image tag path" @(
        '# Compose expects the local worker image tag ai_academy_worker:local.',
        '$env:AVATAR_WORKER_PREBUILT_IMAGE="' + $PrebuiltImage + '"',
        'docker pull $env:AVATAR_WORKER_PREBUILT_IMAGE',
        'docker tag $env:AVATAR_WORKER_PREBUILT_IMAGE ai_academy_worker:local'
    )

    Write-CommandBlock "Start avatar profile after image and models are ready" @(
        '.\scripts\windows-runtime.ps1 -Profile avatar'
    )
}

function Write-SmokeCommands {
    Write-CommandBlock "Read-only model bundle check" @(
        'python .\scripts\check_avatar_models.py'
    )

    Write-CommandBlock "Container import smoke" @(
        'docker compose -f infra\docker-compose.yml --profile avatar run --rm --no-deps --entrypoint python worker-avatar -c "import torch, mmcv, mmdet, mmpose; from mmcv.ops import nms; print(''avatar import smoke ok'')"'
    )

    Write-CommandBlock "Throwaway-queue startup smoke" @(
        'docker compose -f infra\docker-compose.yml --profile avatar run -d --name visus-avatar-startup-smoke --no-deps -e CELERY_AVATAR_QUEUE=avatar-smoke -e CELERY_WORKER_QUEUES=avatar-smoke worker-avatar',
        'docker logs --tail 300 visus-avatar-startup-smoke',
        'docker exec visus-avatar-startup-smoke python -c "import json,urllib.request; d=json.load(urllib.request.urlopen(''http://127.0.0.1:17860/health'')); print(d); assert d.get(''status'') == ''ready''"',
        'docker exec visus-avatar-startup-smoke celery -A worker inspect ping',
        'docker rm -f visus-avatar-startup-smoke'
    )
}

function Write-Plan {
    Write-SafetyBanner

    Write-Section "Runtime split"
    Write-Host "Core runtime does not need avatar dependencies and does not include worker-avatar."
    Write-Host "Avatar runtime needs an avatar-capable ai_academy_worker:local image with CUDA/PyTorch, OpenMMLab, LivePortrait, MuseTalk, and video dependencies inside Docker."
    Write-Host "A missing mmcv error in worker-avatar usually means ai_academy_worker:local is stale/light, for example from a smoke build with INSTALL_OPENMMLAB_DEPS=0. Fix the Docker image, not the Windows .venv."

    Write-Section "Preparation order"
    Write-Host "1. Choose an image path: online OpenMMLab build, local wheel/cache build, pinned wheel URL build, or prebuilt image tag."
    Write-Host "2. Check the MuseTalk/LivePortrait model bundle paths before any worker-avatar start."
    Write-Host "3. Run an explicit container import smoke."
    Write-Host "4. Use a throwaway avatar-smoke queue before allowing worker-avatar to consume the real avatar queue."
    Write-Host "5. Start the avatar profile with .\scripts\windows-runtime.ps1 -Profile avatar only after the image and models are ready."

    Write-BuildCommands
    Write-ModelBundlePaths
    Write-SmokeCommands
}

function Write-CheckResult {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Detail
    )

    $status = if ($Ok) { "PASS" } else { "WARN" }
    Write-Host ("[{0}] {1}: {2}" -f $status, $Name, $Detail)
}

function Invoke-LocalCheck {
    Write-SafetyBanner
    Write-Section "Local file checks"

    $composeFile = Join-Path $RepoRoot "infra\docker-compose.yml"
    $workerDockerfile = Join-Path $RepoRoot "infra\dockerfiles\Dockerfile.worker"
    $runtimeScript = Join-Path $RepoRoot "scripts\windows-runtime.ps1"
    $modelChecker = Join-Path $RepoRoot "scripts\check_avatar_models.py"
    $localWheelPath = Join-Path $RepoRoot ($LocalWheel -replace "/", "\")

    Write-CheckResult "Compose file" (Test-Path $composeFile) "infra\docker-compose.yml"
    Write-CheckResult "Worker Dockerfile" (Test-Path $workerDockerfile) "infra\dockerfiles\Dockerfile.worker"
    Write-CheckResult "Windows runtime wrapper" (Test-Path $runtimeScript) "scripts\windows-runtime.ps1"
    Write-CheckResult "Model checker" (Test-Path $modelChecker) "scripts\check_avatar_models.py"
    Write-CheckResult "Local mmcv wheel" (Test-Path $localWheelPath) $LocalWheel

    Write-Section "Model bundle check"
    $missing = @()
    foreach ($relativePath in $RequiredModelFiles) {
        $fullPath = Join-Path $ModelRoot $relativePath
        if (-not (Test-Path $fullPath)) {
            $missing += $relativePath
        }
    }

    if ($missing.Count -eq 0) {
        Write-CheckResult "MuseTalk required files" $true "All required files exist under $ModelRoot."
    } else {
        Write-CheckResult "MuseTalk required files" $false "Missing $($missing.Count) required file(s) under $ModelRoot."
        foreach ($relativePath in $missing) {
            Write-Host "- $relativePath"
        }
    }

    Write-Section "Next safe commands"
    Write-Host ".\scripts\windows-avatar-runtime.ps1 -Action PrintBuildCommand"
    Write-Host ".\scripts\windows-avatar-runtime.ps1 -Action PrintSmokeCommand"
    Write-Host ""
    Write-Host "Check completed without invoking Docker or starting services."
}

Set-Location $RepoRoot

switch ($Action) {
    "Plan" { Write-Plan }
    "Check" { Invoke-LocalCheck }
    "PrintBuildCommand" {
        Write-SafetyBanner
        Write-BuildCommands
    }
    "PrintSmokeCommand" {
        Write-SafetyBanner
        Write-ModelBundlePaths
        Write-SmokeCommands
    }
}
