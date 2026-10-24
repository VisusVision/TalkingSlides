#!/bin/sh

STORAGE_ROOT="${STORAGE_ROOT:-/app/storage_local}"
APP_RUN_USER="${APP_RUN_USER:-appuser}"
LOCAL_STORAGE_DIR_MODE="${LOCAL_STORAGE_DIR_MODE:-0777}"
LOCAL_STORAGE_PERMISSIVE_CHMOD="${LOCAL_STORAGE_PERMISSIVE_CHMOD:-1}"
LOCAL_STORAGE_REPAIR_PROJECT_DIRS="${LOCAL_STORAGE_REPAIR_PROJECT_DIRS:-1}"

# Refuse overlap before the legacy public-storage repair can chmod anything.
# A missing/unprovisioned private volume does not block ordinary lesson startup.
if [ -n "${VISUAL_RENDER_PRIVATE_ROOT:-}" ]; then
    if [ "$(id -u "$APP_RUN_USER" 2>/dev/null)" != "1000" ] || [ "$(id -g "$APP_RUN_USER" 2>/dev/null)" != "1000" ]; then
        echo "ERROR: private storage requires application UID/GID 1000:1000" >&2
        exit 78
    fi
    python -c 'import os; from pathlib import Path; a=Path(os.environ["VISUAL_RENDER_PRIVATE_ROOT"]).resolve(); b=Path(os.environ.get("STORAGE_ROOT", "/app/storage_local")).resolve(); assert not a.is_relative_to(b) and not b.is_relative_to(a), "private/public storage overlap"' || exit 78
fi

if [ -n "$STORAGE_ROOT" ]; then
    if mkdir -p "$STORAGE_ROOT"; then
        if [ "$LOCAL_STORAGE_PERMISSIVE_CHMOD" = "1" ]; then
            chmod "$LOCAL_STORAGE_DIR_MODE" "$STORAGE_ROOT" 2>/dev/null || echo "WARNING: chmod failed for $STORAGE_ROOT" >&2
            if [ "$LOCAL_STORAGE_REPAIR_PROJECT_DIRS" = "1" ]; then
                for project_dir in "$STORAGE_ROOT"/[0-9]*; do
                    if [ -d "$project_dir" ]; then
                        chmod "$LOCAL_STORAGE_DIR_MODE" "$project_dir" 2>/dev/null || echo "WARNING: chmod failed for $project_dir" >&2
                        if [ -d "$project_dir/subtitles" ]; then
                            chmod "$LOCAL_STORAGE_DIR_MODE" "$project_dir/subtitles" 2>/dev/null || echo "WARNING: chmod failed for $project_dir/subtitles" >&2
                        fi
                    fi
                done
            fi
        fi
    else
        echo "WARNING: could not create STORAGE_ROOT at $STORAGE_ROOT" >&2
    fi
fi

if [ "$#" -eq 0 ]; then
    echo "ERROR: no command specified for local-storage-entrypoint.sh" >&2
    exit 64
fi

if command -v runuser >/dev/null 2>&1; then
    if id "$APP_RUN_USER" >/dev/null 2>&1; then
        exec runuser -u "$APP_RUN_USER" -- "$@"
    fi
fi

if [ -n "${VISUAL_RENDER_PRIVATE_ROOT:-}" ] && [ "$(id -u)" = "0" ]; then
    echo "ERROR: refusing private-enabled service without privilege drop" >&2
    exit 78
fi

exec "$@"
