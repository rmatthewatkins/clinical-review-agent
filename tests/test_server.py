"""Tests for the FastAPI server endpoints."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from clinical_review_agent.schema import get_connection, ALL_TABLES
from tests.conftest import TEST_DATABASE_URL


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def _server_db():
    """Set up a clean test database and populate with sample data."""
    conn = get_connection(TEST_DATABASE_URL)
    for table in ALL_TABLES:
        conn.execute(f"TRUNCATE {table} CASCADE")
    conn.commit()

    # Insert test data
    conn.execute(
        "INSERT INTO patients (id, birth_date, gender, race, ethnicity, city, state) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        ("P001", "1955-06-15", "M", "white", "nonhispanic", "Springfield", "IL"),
    )
    conn.execute(
        """INSERT INTO encounters (id, patient_id, type, start, "end", reason_code, reason_display, discharge_disposition) """
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        ("E001", "P001", "inpatient", "2025-01-10", "2025-01-14", "I50.9", "Heart failure", "home"),
    )
    conn.execute(
        """INSERT INTO encounters (id, patient_id, type, start, "end", reason_code, reason_display, discharge_disposition) """
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        ("E003", "P001", "inpatient", "2025-01-28", "2025-02-02", "I50.9", "Heart failure", "home"),
    )
    conn.execute(
        "INSERT INTO conditions (id, patient_id, encounter_id, code, display, onset, abatement, clinical_status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        ("C001", "P001", "E001", "I50.9", "Heart failure", "2025-01-10", None, "active"),
    )
    conn.execute(
        "INSERT INTO readmissions (id, index_encounter_id, readmission_encounter_id, days_between, patient_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (1, "E001", "E003", 14, "P001"),
    )
    conn.execute(
        "INSERT INTO reviews (case_type, case_id, structured_json, clinical_narrative, model_used, created_at, tokens_used) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            "readmission",
            1,
            json.dumps({
                "root_cause_category": "inadequate_transition_planning",
                "preventability_score": 4,
                "preventability_rationale": "Test rationale.",
                "contributing_factors": ["No follow-up scheduled"],
                "recommended_interventions": ["Schedule cardiology follow-up"],
                "confidence_level": "high",
            }),
            "Test narrative.",
            "claude-sonnet-4-6",
            "2025-02-01T00:00:00Z",
            1500,
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def client(_server_db):
    """Create a FastAPI test client with a real database pool."""
    from psycopg_pool import ConnectionPool
    from psycopg.rows import dict_row
    import server as server_mod

    pool = ConnectionPool(
        TEST_DATABASE_URL,
        min_size=1,
        max_size=2,
        open=True,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
    pool.wait()

    server_mod._pool = pool
    executor = ThreadPoolExecutor(max_workers=2)
    server_mod._executor = executor
    try:
        yield TestClient(server_mod.app, raise_server_exceptions=False)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        server_mod._executor = None
        # Clean up job tracking state
        server_mod._jobs.clear()
        server_mod._active_reviews.clear()
        pool.close()
        server_mod._pool = None


# ── Health check tests ───────────────────────────────────────────────────


class TestHealthChecks:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readiness(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"


# ── Summary tests ────────────────────────────────────────────────────────


class TestSummary:
    def test_summary(self, client):
        resp = client.get("/api/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["patients"] == 1
        assert data["encounters"] == 2
        assert data["readmissions"] == 1
        assert data["reviews"] == 1
        assert "root_cause_distribution" in data
        assert "preventability_score_distribution" in data


# ── Readmissions tests ──────────────────────────────────────────────────


class TestReadmissions:
    def test_list_readmissions(self, client):
        resp = client.get("/api/readmissions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        row = data["items"][0]
        assert row["readmission_id"] == 1
        assert row["patient_id"] == "P001"
        assert row["days_between"] == 14
        assert row["review_status"] == "reviewed"

    def test_list_readmissions_pagination(self, client):
        resp = client.get("/api/readmissions?offset=0&limit=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 1
        assert len(data["items"]) == 1

    def test_list_readmissions_offset_beyond(self, client):
        resp = client.get("/api/readmissions?offset=100")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 0

    def test_readmission_detail(self, client):
        resp = client.get("/api/readmissions/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "context" in data
        assert "review" in data
        assert data["context"]["readmission_id"] == 1

    def test_readmission_detail_not_found(self, client):
        resp = client.get("/api/readmissions/9999")
        assert resp.status_code == 404


# ── Reviews tests ────────────────────────────────────────────────────────


class TestReviews:
    def test_list_reviews(self, client):
        resp = client.get("/api/reviews")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        review = data["items"][0]
        assert review["readmission_id"] == 1
        assert review["root_cause"] == "inadequate_transition_planning"
        assert review["preventability_score"] == 4

    def test_list_reviews_pagination(self, client):
        resp = client.get("/api/reviews?offset=0&limit=1")
        data = resp.json()
        assert data["limit"] == 1

    def test_review_detail(self, client):
        resp = client.get("/api/reviews/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["review"] is not None
        assert data["review"]["structured"]["root_cause_category"] == "inadequate_transition_planning"
        assert data["review"]["clinical_narrative"] == "Test narrative."

    def test_review_detail_typed(self, client):
        resp = client.get("/api/reviews/readmission/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["review"] is not None
        assert data["review"]["structured"]["root_cause_category"] == "inadequate_transition_planning"

    def test_review_detail_not_found(self, client):
        resp = client.get("/api/reviews/9999")
        assert resp.status_code == 404


# ── Analytics tests ──────────────────────────────────────────────────────


class TestAnalytics:
    def test_analytics(self, client):
        resp = client.get("/api/analytics")
        assert resp.status_code == 200
        data = resp.json()
        assert "contributing_factors" in data
        assert "recommended_interventions" in data

    def test_by_root_cause(self, client):
        resp = client.get("/api/analytics/by-root-cause")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["category"] == "inadequate_transition_planning"

    def test_by_diagnosis(self, client):
        resp = client.get("/api/analytics/by-diagnosis")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_by_gender(self, client):
        resp = client.get("/api/analytics/by-gender")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_by_age_group(self, client):
        resp = client.get("/api/analytics/by-age-group")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_by_days_between(self, client):
        resp = client.get("/api/analytics/by-days-between")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ── Review trigger tests ────────────────────────────────────────────────


class TestTriggerReview:
    def test_trigger_not_found(self, client):
        resp = client.post("/api/reviews/9999/run")
        assert resp.status_code == 404

    def test_trigger_rate_limit(self, client):
        """Exhaust the rate limiter and verify 429 response."""
        import server as server_mod
        # Set rate limit to 0 to trigger immediately
        server_mod._review_limiter.max_per_minute = 0
        resp = client.post("/api/reviews/1/run")
        assert resp.status_code == 429
        # Restore
        server_mod._review_limiter.max_per_minute = 10

    @patch("clinical_review_agent.agent.reviewer._call_claude")
    @patch("clinical_review_agent.agent.reviewer._store_review")
    def test_trigger_returns_202_with_job_id(self, mock_store, mock_call, client):
        """POST /api/reviews/{id}/run returns 202 with a job_id."""
        mock_call.return_value = (
            '```json\n{"root_cause_category": "test", "preventability_score": 3}\n```\nNarrative.',
            500,
        )
        mock_store.return_value = None

        resp = client.post("/api/reviews/1/run")
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert data.get("case_id") == 1 or data.get("readmission_id") == 1

    @patch("clinical_review_agent.agent.reviewer._call_claude")
    @patch("clinical_review_agent.agent.reviewer._store_review")
    def test_poll_job_until_completed(self, mock_store, mock_call, client):
        """GET /api/jobs/{id} shows running then completed."""
        mock_call.return_value = (
            '```json\n{"root_cause_category": "test", "preventability_score": 3}\n```\nNarrative.',
            500,
        )
        mock_store.return_value = None

        resp = client.post("/api/reviews/1/run")
        job_id = resp.json()["job_id"]

        # Poll until completed (max 10s)
        deadline = time.monotonic() + 10
        final_status = None
        while time.monotonic() < deadline:
            poll = client.get(f"/api/jobs/{job_id}")
            assert poll.status_code == 200
            final_status = poll.json()["status"]
            if final_status in ("completed", "failed"):
                break
            time.sleep(0.2)

        assert final_status == "completed"
        result = poll.json()["result"]
        assert result["root_cause_category"] == "test"
        assert result["preventability_score"] == 3

    @patch("clinical_review_agent.agent.reviewer._call_claude")
    @patch("clinical_review_agent.agent.reviewer._store_review")
    def test_trigger_dedup_409(self, mock_store, mock_call, client):
        """Second POST for same readmission returns 409 while first is running."""
        # Use an event to block the background thread until we've tested the 409
        gate = threading.Event()
        mock_call.side_effect = lambda prompt: (gate.wait(timeout=5), ("```json\n{}\n```\n", 100))[1]
        mock_store.return_value = None

        resp1 = client.post("/api/reviews/1/run")
        assert resp1.status_code == 202

        # Second request while first is still running
        resp2 = client.post("/api/reviews/1/run")
        assert resp2.status_code == 409

        # Verify 409 body includes job_id
        detail = resp2.json()["detail"]
        assert "job_id" in detail
        assert detail["job_id"] == resp1.json()["job_id"]

        # Release the background thread so it finishes cleanly
        gate.set()

    def test_job_not_found(self, client):
        """GET /api/jobs/{unknown} returns 404."""
        resp = client.get("/api/jobs/nonexistent")
        assert resp.status_code == 404

    @patch("clinical_review_agent.agent.reviewer._call_claude")
    @patch("clinical_review_agent.agent.reviewer._store_review")
    def test_list_jobs(self, mock_store, mock_call, client):
        """GET /api/jobs lists all tracked jobs."""
        mock_call.return_value = (
            '```json\n{"root_cause_category": "test", "preventability_score": 3}\n```\nNarrative.',
            500,
        )
        mock_store.return_value = None

        # Submit a job
        client.post("/api/reviews/1/run")

        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        assert len(data["jobs"]) >= 1
        assert data["jobs"][0].get("readmission_id") == 1 or data["jobs"][0].get("case_id") == 1
