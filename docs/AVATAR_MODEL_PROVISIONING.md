# Avatar Model Provisioning

This runbook covers the local MuseTalk model bundle required before starting
`worker-avatar`.

The repository does not ship model weights. Do not commit model files,
download caches, generated media, or hash manifests containing private local
paths unless they are explicitly intended as documentation.

## Required layout

Place the MuseTalk model bundle under the repo-root local storage directory:

```text
storage_local/models/
+-- musetalk/
|   +-- musetalk.json
+-- musetalkV15/
|   +-- unet.pth
+-- sd-vae/
|   +-- config.json
|   +-- diffusion_pytorch_model.bin
+-- whisper/
|   +-- config.json
|   +-- pytorch_model.bin
|   +-- preprocessor_config.json
+-- dwpose/
|   +-- dw-ll_ucoco_384.pth
+-- face-parse-bisent/
    +-- 79999_iter.pth
    +-- resnet18-5c106cde.pth
```

`musetalk/musetalk.json` is required even though older bootstrap checks only
reported the nine large model files. The persistent MuseTalk service builds a
runtime `models/musetalk/config.json` from it. A pre-existing
`musetalk/config.json` is reported by the checker as an optional normalized
runtime artifact, but it does not replace `musetalk/musetalk.json` for
provisioning readiness.

Optional upstream files may also appear in a complete upstream checkout:

```text
musetalk/pytorch_model.bin
musetalkV15/musetalk.json
syncnet/latentsync_syncnet.pt
```

They are not required by the current local avatar path, but the checker reports
whether they are present.

## Known upstream sources

These are source locations to review when building a model bundle. They are not
a checksum trust statement.

- `TMElyralab/MuseTalk`
- `stabilityai/sd-vae-ft-mse`
- `openai/whisper-tiny`
- `yzd-v/DWPose`
- `ByteDance/LatentSync` for optional SyncNet assets
- face-parse-bisent weights from the upstream MuseTalk instructions
- ResNet18 from PyTorch model hosting

The upstream MuseTalk repository includes download scripts, but they are not
pinned to immutable revisions and do not provide checksum verification. Do not
use them as an unattended production provisioning mechanism without an external
pinning and hash review process.

## Readiness checker

Run the local checker before starting `worker-avatar`:

```powershell
python .\scripts\check_avatar_models.py
```

To check a different root:

```powershell
python .\scripts\check_avatar_models.py C:\path\to\models
```

For automation:

```powershell
python .\scripts\check_avatar_models.py --json
```

Expected behavior:

- exit `0` when the bundle is complete;
- exit nonzero when the root is missing, a required file is missing, a required
  file is empty, or `musetalk/musetalk.json` is missing or empty;
- report `missing_files`, `empty_files`, MuseTalk config status, optional
  files, warnings, and errors.

## Record local hashes

After provisioning, record hashes outside the repository or in an explicitly
reviewed operations system:

```powershell
Get-ChildItem .\storage_local\models -Recurse -File |
  Get-FileHash -Algorithm SHA256 |
  Sort-Object Path
```

Linux/container equivalent:

```bash
find storage_local/models -type f -print0 |
  sort -z |
  xargs -0 sha256sum
```

Hash recording proves local repeatability. It does not prove upstream
authenticity unless the expected hashes came from a trusted review.

## Do not start the normal avatar worker first

Do not start the normal `worker-avatar` service until the checker passes.

If the local Redis `avatar` queue already contains pending preview or render
jobs, a normal `worker-avatar` start will consume them immediately. Use a
throwaway queue for startup validation.

Check the queue depth:

```powershell
docker compose -f infra\docker-compose.yml exec -T redis redis-cli LLEN avatar
```

## Isolated startup smoke

After the checker passes, validate startup without consuming the normal
`avatar` queue:

```powershell
docker compose -f infra\docker-compose.yml run -d `
  --name visus-avatar-startup-smoke `
  --no-deps `
  -e CELERY_AVATAR_QUEUE=avatar-smoke `
  -e CELERY_WORKER_QUEUES=avatar-smoke `
  worker-avatar
```

Inspect logs:

```powershell
docker logs --tail 300 visus-avatar-startup-smoke
```

Check MuseTalk service health:

```powershell
docker exec visus-avatar-startup-smoke python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:17860/health')); print(d); assert d.get('status') == 'ready'"
```

Check Celery responsiveness on the throwaway worker:

```powershell
docker exec visus-avatar-startup-smoke celery -A worker inspect ping
```

Clean up the smoke container:

```powershell
docker rm -f visus-avatar-startup-smoke
```

## GPU caveat

The local avatar path requires a validated NVIDIA Docker runtime. An 8 GB GPU
can be useful for startup and short smoke tests, but full
LivePortrait-plus-MuseTalk workloads may still hit VRAM pressure, slow
timeouts, or downscale paths. Keep avatar worker concurrency at `1` per GPU
until real workload benchmarks prove otherwise.

## Validation order

Use this order so failures stay small and attributable:

1. Run the model checker.
2. Run isolated worker startup on a throwaway queue.
3. Confirm MuseTalk `/health` reports `status=ready`.
4. Run an avatar preview smoke.
5. Run a full avatar render smoke.

Only after those pass should the normal `worker-avatar` service consume the
real `avatar` queue.
