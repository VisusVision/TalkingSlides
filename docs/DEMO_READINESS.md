# VISUS VidLab Demo Readiness Seed Data

Use the local Django management command to create repeatable university demo data:

```powershell
cd services/api
..\..\.venv\Scripts\python.exe manage.py seed_demo_data --reset-demo --with-moderation-fixtures --with-analytics-activity
cd ..\..
```

For a running Docker environment, run the seed explicitly after startup:

```powershell
docker compose -f infra/docker-compose.yml exec api python manage.py seed_demo_data
```

Useful variants:

```powershell
python manage.py seed_demo_data
python manage.py seed_demo_data --reset-demo
python manage.py seed_demo_data --with-moderation-fixtures --run-moderation
python manage.py seed_demo_data --with-analytics-activity --run-intelligence
```

The default local password is `visus-demo-local`. The command prints the effective deterministic password for all demo accounts, and you can override it for a local run with `VISUS_DEMO_PASSWORD`. Docker/API startup intentionally does not run this command or create demo users automatically.

## Demo Accounts

Publishers:

- `jane.doe.demo@example.com` - Jane Doe, biology and academic writing instructor
- `ahmet.yilmaz.demo@example.com` - Ahmet Yılmaz, Turkish STEM educator
- `demo.tech.teacher@example.com` - Demo Tech Teacher, technical lessons

Students:

- `demo.student.active@example.com`
- `demo.student.struggling@example.com`
- `demo.student.commenter@example.com`

Staff:

- `demo.staff@example.com`

## Seeded Lessons

- `Introduction to Photosynthesis` - short English biology lesson
- `Bitkilerde Fotosentez ve Enerji Üretimi` - medium Turkish science lesson
- `Cell Structure and Organelles` - long English biology lesson for chunking smoke
- `Introduction to Neural Network Optimization` - dense technical lesson
- `Vague Notes About Databases` - intentionally poor-quality fixture
- `How to Write a Strong Academic Abstract` - well-structured academic writing fixture
- `Understanding World War II in Historical Context` - neutral historical educational lesson

The lessons are published, render-ready at the metadata level, and have transcript pages plus completed `video_export` job rows so catalog, studio, analytics, and watch APIs can exercise realistic records without launching render/avatar jobs.

## Analytics

Analytics activity is seeded by default. It includes progress, likes, and comments that create visible patterns:

- strong completion and positive comments for structured lessons
- lower progress and example requests for vague or complex lessons
- mixed progress on medium-length lessons

Viewer identity should stay aggregated by existing analytics endpoints.

## Moderation Fixtures

Pass `--with-moderation-fixtures` to create safe local moderation fixtures. Pass `--run-moderation` to run the existing local text moderation orchestrator synchronously and print `allowed`, `needs_review`, `blocked`, or `unknown` status per fixture.

The OCR fixture creates a simple local image under runtime storage. If OCR/provider support is unavailable, the command prints `moderation provider unavailable` and leaves the fixture ready for manual scan.

## Do Not Commit Runtime Artifacts

Before committing, verify `git status --short` and do not stage:

- `services/api/db.sqlite3`
- `services/api/media/`
- `storage_local/` or `services/storage_local/`
- generated OCR/media files
- environment files
- frontend build caches
