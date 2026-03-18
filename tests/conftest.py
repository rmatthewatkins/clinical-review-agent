"""Shared test fixtures for PostgreSQL-backed tests.

Set TEST_DATABASE_URL to point at a test PostgreSQL database.
Falls back to a local readmissions_test database — NEVER uses the production Railway DB.
"""

import os

import pytest

import src.schema as schema_mod
from src.schema import ALL_TABLES

# Use a dedicated test database — never default to production Railway
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://localhost/readmissions_test",
)


@pytest.fixture(autouse=True)
def _patch_database_url(monkeypatch):
    """Point all get_connection() calls at the test database."""
    monkeypatch.setattr(schema_mod, "DATABASE_URL", TEST_DATABASE_URL)


@pytest.fixture
def db_conn():
    """Provide a clean test database connection with schema initialized.

    Truncates all tables before the test and closes the connection after.
    """
    from src.schema import get_connection
    conn = get_connection(TEST_DATABASE_URL)
    # Truncate all tables for a clean slate
    for table in ALL_TABLES:
        conn.execute(f"TRUNCATE {table} CASCADE")
    conn.commit()
    yield conn
    conn.close()
