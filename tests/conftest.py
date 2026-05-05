"""Shared test fixtures for PostgreSQL-backed tests.

Set TEST_DATABASE_URL to point at a test PostgreSQL database.
Falls back to a local readmissions_test database — NEVER uses the production Railway DB.
"""

import os

import pytest

import clinical_review_agent.schema as schema_mod
import clinical_review_agent.config as config_mod
from clinical_review_agent.schema import ALL_TABLES

# Use a dedicated test database — never default to production Railway
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://localhost/readmissions_test",
)


@pytest.fixture(autouse=True)
def _patch_database_url(monkeypatch):
    """Point all get_connection() calls at the test database."""
    monkeypatch.setattr(schema_mod, "DATABASE_URL", TEST_DATABASE_URL)
    # Also patch config so get_settings() doesn't fail on missing DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    # Reset the config singleton so it reloads with the test URL
    monkeypatch.setattr(config_mod, "settings", None)


@pytest.fixture
def db_conn():
    """Provide a clean test database connection with schema initialized.

    Truncates all tables before the test and closes the connection after.
    """
    from clinical_review_agent.schema import get_connection
    conn = get_connection(TEST_DATABASE_URL)
    # Truncate all tables for a clean slate
    for table in ALL_TABLES:  # includes mortality_cases
        conn.execute(f"TRUNCATE {table} CASCADE")
    conn.commit()
    yield conn
    conn.close()
