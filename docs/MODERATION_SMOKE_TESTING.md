# Moderation Smoke Testing

Use these commands from the API directory:

```powershell
cd services/api
```

Create a clean project and scan it synchronously:

```powershell
..\..\.venv\Scripts\python.exe manage.py create_moderation_smoke_project --kind clean --scan
```

Create a blocked project:

```powershell
..\..\.venv\Scripts\python.exe manage.py create_moderation_smoke_project --kind profanity --scan
..\..\.venv\Scripts\python.exe manage.py create_moderation_smoke_project --kind violence --scan
```

Run or rerun a scan for an existing project:

```powershell
..\..\.venv\Scripts\python.exe manage.py run_moderation_scan --project-id 123 --sync
..\..\.venv\Scripts\python.exe manage.py run_moderation_scan --project-id 123 --phase manual_rescan --sync
```

Create a review-needed project and request admin review:

```powershell
..\..\.venv\Scripts\python.exe manage.py create_moderation_smoke_project --kind ambiguous-review --scan --request-review --review-message "AI misunderstood educational context"
```

Create a review request for an existing reviewable project:

```powershell
..\..\.venv\Scripts\python.exe manage.py create_moderation_review_request --project-id 123 --user-id 1 --message "AI misunderstood educational context"
```

Approve or reject from Django admin:

1. Start the API server.
2. Open `/admin/ai_agents/adminreviewrequest/`.
3. Select open review requests.
4. Use the action menu to approve or reject selected open requests.

To reset a local SQLite database, stop the API/worker processes, remove the local SQLite database file configured by the current environment, then run migrations again:

```powershell
..\..\.venv\Scripts\python.exe manage.py migrate
```

Check the active database path in `services/api/config/settings.py` and environment variables before deleting anything.
