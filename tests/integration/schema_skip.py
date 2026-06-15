import pytest
from django.db import connection


STALE_SCHEMA_SKIP_MESSAGE = "Local DB schema is stale; run migrations to execute this test."


def table_has_column(table_name: str, column_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
    return any(row[1] == column_name for row in rows)


def skip_if_column_missing(table_name: str, column_name: str) -> None:
    if not table_has_column(table_name, column_name):
        pytest.skip(STALE_SCHEMA_SKIP_MESSAGE)
