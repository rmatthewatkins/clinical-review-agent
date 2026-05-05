"""Centralized application configuration.

All settings are loaded from environment variables with sensible defaults
for local development. Production deployments should set env vars explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    # ── Database ─────────────────────────────────────────────────────────
    database_url: str = ""

    # Connection pool (server only)
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    db_connect_timeout: int = 10

    # ── Claude API ───────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"
    claude_max_tokens: int = 4096
    claude_max_retries: int = 5
    claude_retry_base_delay: float = 1.0
    claude_retry_max_delay: float = 60.0

    # ── FHIR source ──────────────────────────────────────────────────────
    fhir_page_size: int = 100
    fhir_patient_batch_size: int = 50
    fhir_timeout: float = 30.0
    fhir_max_retries: int = 5

    # ── Clinical logic ───────────────────────────────────────────────────
    readmission_window_days: int = 30
    readmission_min_days: int = 2
    readmission_exclude_transfer_dispositions: bool = True
    readmission_exclude_surgical_followup: bool = True

    # ── Mortality ───────────────────────────────────────────────────────
    mortality_lookback_days: int = 90

    # ── Server ───────────────────────────────────────────────────────────
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # Rate limiting (reviews per minute)
    review_rate_limit: int = 10

    # ── Logging ──────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "json"  # "json" or "text"


def load_settings() -> Settings:
    """Load settings from environment variables.

    Raises ``RuntimeError`` if DATABASE_URL is not set (no hardcoded fallback).
    """
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL environment variable is required. "
            "Set it to a PostgreSQL connection string, e.g.: "
            "postgresql://user:pass@host:5432/dbname"
        )

    cors_raw = os.environ.get("CORS_ORIGINS", "http://localhost:3000")
    cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]

    return Settings(
        database_url=database_url,
        db_pool_min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "2")),
        db_pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "10")),
        db_connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "10")),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        claude_model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
        claude_max_tokens=int(os.environ.get("CLAUDE_MAX_TOKENS", "4096")),
        claude_max_retries=int(os.environ.get("CLAUDE_MAX_RETRIES", "5")),
        claude_retry_base_delay=float(os.environ.get("CLAUDE_RETRY_BASE_DELAY", "1.0")),
        claude_retry_max_delay=float(os.environ.get("CLAUDE_RETRY_MAX_DELAY", "60.0")),
        fhir_page_size=int(os.environ.get("FHIR_PAGE_SIZE", "100")),
        fhir_patient_batch_size=int(os.environ.get("FHIR_PATIENT_BATCH_SIZE", "50")),
        fhir_timeout=float(os.environ.get("FHIR_TIMEOUT", "30.0")),
        fhir_max_retries=int(os.environ.get("FHIR_MAX_RETRIES", "5")),
        readmission_window_days=int(os.environ.get("READMISSION_WINDOW_DAYS", "30")),
        readmission_min_days=int(os.environ.get("READMISSION_MIN_DAYS", "2")),
        readmission_exclude_transfer_dispositions=os.environ.get("READMISSION_EXCLUDE_TRANSFER_DISPOSITIONS", "true").lower() in ("true", "1", "yes"),
        readmission_exclude_surgical_followup=os.environ.get("READMISSION_EXCLUDE_SURGICAL_FOLLOWUP", "true").lower() in ("true", "1", "yes"),
        mortality_lookback_days=int(os.environ.get("MORTALITY_LOOKBACK_DAYS", "90")),
        cors_origins=cors_origins,
        server_host=os.environ.get("SERVER_HOST", "0.0.0.0"),
        server_port=int(os.environ.get("SERVER_PORT", "8000")),
        review_rate_limit=int(os.environ.get("REVIEW_RATE_LIMIT", "10")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        log_format=os.environ.get("LOG_FORMAT", "json"),
    )


# Singleton — import and use `settings` directly.
# For tests, monkeypatch src.config.settings or individual fields.
settings: Settings | None = None


def get_settings() -> Settings:
    """Return the global settings, loading on first call."""
    global settings
    if settings is None:
        settings = load_settings()
    return settings
