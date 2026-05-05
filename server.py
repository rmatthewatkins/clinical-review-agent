"""FastAPI backend for the clinical review agent."""

import json
import logging
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date
from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from clinical_review_agent.config import get_settings
from clinical_review_agent.logging_config import setup_logging
from clinical_review_agent.schema import MIGRATION_SQL, SCHEMA_SQL
from clinical_review_agent.analytics.analyze import (
    _fetch_reviews,
    _fetch_diagnosis_for_readmission,
    root_cause_distribution,
    preventability_score_distribution,
    mean_preventability_by_diagnosis,
    top_contributing_factors,
    top_recommended_interventions,
)
from clinical_review_agent.agent.context import assemble_context

logger = logging.getLogger(__name__)

# ── Connection pool (initialized at startup) ────────────────────────────

_pool: ConnectionPool | None = None

# ── Background job tracking ─────────────────────────────────────────────


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


_MAX_JOBS = 500

_jobs: dict[str, dict] = {}
_active_reviews: dict[tuple[str, int], str] = {}  # (case_type, case_id) -> job_id
_jobs_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None


def get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("Connection pool not initialized — server not started?")
    return _pool


# ── Rate limiter (simple token bucket, in-memory) ───────────────────────


class _RateLimiter:
    """Simple per-endpoint token-bucket rate limiter (thread-safe)."""

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def check(self) -> bool:
        with self._lock:
            now = time.monotonic()
            # Purge entries older than 60s
            self._timestamps = [t for t in self._timestamps if now - t < 60]
            if len(self._timestamps) >= self.max_per_minute:
                return False
            self._timestamps.append(now)
            return True


# ── App lifecycle ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool, _executor
    cfg = get_settings()

    setup_logging(level=cfg.log_level, fmt=cfg.log_format)
    logger.info("Starting clinical review API server")

    _pool = ConnectionPool(
        cfg.database_url,
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
        open=True,
        kwargs={"row_factory": dict_row, "autocommit": True, "connect_timeout": cfg.db_connect_timeout},
    )
    _pool.wait()

    # Ensure schema exists (once at startup)
    with _pool.connection() as conn:
        conn.execute(MIGRATION_SQL)
        conn.execute(SCHEMA_SQL)

    _executor = ThreadPoolExecutor(max_workers=3)

    logger.info("Connection pool ready (min=%d, max=%d)", cfg.db_pool_min_size, cfg.db_pool_max_size)
    yield

    logger.info("Shutting down executor and connection pool")
    _executor.shutdown(wait=True, cancel_futures=True)
    _executor = None
    _pool.close()
    _pool = None


app = FastAPI(title="Clinical Review Agent API", lifespan=lifespan)

# CORS — configurable via CORS_ORIGINS env var
_cfg = None
try:
    _cfg = get_settings()
except RuntimeError:
    pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cfg.cors_origins if _cfg else ["http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Rate limiter for the review endpoint
_review_limiter = _RateLimiter(max_per_minute=_cfg.review_rate_limit if _cfg else 10)


# ── Global error handler ────────────────────────────────────────────────


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ── Request logging middleware ───────────────────────────────────────────


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info(
        "%s %s -> %d (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        extra={"method": request.method, "endpoint": request.url.path,
               "status_code": response.status_code, "duration_ms": duration_ms},
    )
    return response


# ── Health checks ────────────────────────────────────────────────────────


@app.get("/health")
def health_check():
    """Basic liveness probe."""
    return {"status": "ok"}


@app.get("/ready")
def readiness_check():
    """Readiness probe — verifies database connectivity."""
    try:
        pool = get_pool()
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ready", "database": "connected"}
    except Exception as exc:
        logger.warning("Readiness check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "database": str(exc)},
        )


# ── Helpers ──────────────────────────────────────────────────────────────


def _compute_age(birth_date_str: str | None) -> int | None:
    if not birth_date_str:
        return None
    try:
        bd = date.fromisoformat(birth_date_str)
        today = date.today()
        return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
    except (ValueError, TypeError):
        return None


# ── Dashboard ────────────────────────────────────────────────────────────


@app.get("/api/summary")
def get_summary():
    pool = get_pool()
    with pool.connection() as conn:
        patients = conn.execute("SELECT COUNT(*) AS cnt FROM patients").fetchone()["cnt"]
        encounters = conn.execute("SELECT COUNT(*) AS cnt FROM encounters").fetchone()["cnt"]
        readmission_count = conn.execute("SELECT COUNT(*) AS cnt FROM readmissions").fetchone()["cnt"]
        mortality_count = conn.execute("SELECT COUNT(*) AS cnt FROM mortality_cases").fetchone()["cnt"]
        review_count = conn.execute("SELECT COUNT(*) AS cnt FROM reviews").fetchone()["cnt"]
        readmission_review_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM reviews WHERE case_type = 'readmission'"
        ).fetchone()["cnt"]
        mortality_review_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM reviews WHERE case_type = 'mortality'"
        ).fetchone()["cnt"]

        reviews = _fetch_reviews(conn)
        rc = root_cause_distribution(reviews)
        sd = preventability_score_distribution(reviews)
        mpd = mean_preventability_by_diagnosis(conn, reviews)
        top_10_mpd = dict(list(mpd.items())[:10])

    return {
        "patients": patients,
        "encounters": encounters,
        "readmissions": readmission_count,
        "mortality_cases": mortality_count,
        "reviews": review_count,
        "cases": {
            "readmission": readmission_count,
            "mortality": mortality_count,
        },
        "reviews_by_type": {
            "readmission": readmission_review_count,
            "mortality": mortality_review_count,
        },
        "root_cause_distribution": rc,
        "preventability_score_distribution": sd,
        "mean_preventability_by_diagnosis": top_10_mpd,
    }


# ── Reviews list ─────────────────────────────────────────────────────────


@app.get("/api/reviews")
def get_reviews(
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    case_type: str | None = Query(None, description="Filter by case type"),
):
    pool = get_pool()
    with pool.connection() as conn:
        # Get total count
        if case_type:
            total = conn.execute(
                "SELECT COUNT(*) AS cnt FROM reviews WHERE case_type = %s", (case_type,)
            ).fetchone()["cnt"]
        else:
            total = conn.execute("SELECT COUNT(*) AS cnt FROM reviews").fetchone()["cnt"]

        # Build query based on case_type filter
        if case_type:
            rows = conn.execute(
                """SELECT r.case_type, r.case_id, r.readmission_id, r.structured_json, r.created_at,
                          p.birth_date, p.gender,
                          c.display AS index_diagnosis
                   FROM reviews r
                   LEFT JOIN readmissions ra ON r.case_type = 'readmission' AND ra.id = r.case_id
                   LEFT JOIN mortality_cases mc ON r.case_type = 'mortality' AND mc.id = r.case_id
                   JOIN patients p ON p.id = COALESCE(ra.patient_id, mc.patient_id)
                   LEFT JOIN LATERAL (
                       SELECT display FROM conditions
                       WHERE encounter_id = COALESCE(ra.index_encounter_id, mc.encounter_id)
                       ORDER BY onset ASC LIMIT 1
                   ) c ON true
                   WHERE r.case_type = %s
                   ORDER BY r.id DESC
                   OFFSET %s LIMIT %s""",
                (case_type, offset, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT r.case_type, r.case_id, r.readmission_id, r.structured_json, r.created_at,
                          p.birth_date, p.gender,
                          c.display AS index_diagnosis
                   FROM reviews r
                   LEFT JOIN readmissions ra ON r.case_type = 'readmission' AND ra.id = r.case_id
                   LEFT JOIN mortality_cases mc ON r.case_type = 'mortality' AND mc.id = r.case_id
                   JOIN patients p ON p.id = COALESCE(ra.patient_id, mc.patient_id)
                   LEFT JOIN LATERAL (
                       SELECT display FROM conditions
                       WHERE encounter_id = COALESCE(ra.index_encounter_id, mc.encounter_id)
                       ORDER BY onset ASC LIMIT 1
                   ) c ON true
                   ORDER BY r.id DESC
                   OFFSET %s LIMIT %s""",
                (offset, limit),
            ).fetchall()

    results = []
    for r in rows:
        parsed = json.loads(r["structured_json"]) if r["structured_json"] else {}
        results.append({
            "case_type": r["case_type"],
            "case_id": r["case_id"],
            "readmission_id": r["readmission_id"] or r["case_id"],
            "age": _compute_age(r["birth_date"]),
            "gender": r["gender"],
            "index_diagnosis": r["index_diagnosis"] or "Unknown",
            "root_cause": parsed.get("root_cause_category", "Unknown"),
            "preventability_score": parsed.get("preventability_score"),
            "confidence": parsed.get("confidence_level"),
            "reviewed_at": r["created_at"],
        })
    return {"total": total, "offset": offset, "limit": limit, "items": results}


# ── Single review detail ─────────────────────────────────────────────────


@app.get("/api/reviews/{case_type}/{case_id}")
def get_review_detail_by_type(case_type: str, case_id: int):
    """Review detail by case type and case ID."""
    pool = get_pool()
    with pool.connection() as conn:
        if case_type == "readmission":
            case_row = conn.execute(
                "SELECT * FROM readmissions WHERE id = %s", (case_id,)
            ).fetchone()
            if not case_row:
                raise HTTPException(status_code=404, detail="Readmission not found")
        elif case_type == "mortality":
            case_row = conn.execute(
                "SELECT * FROM mortality_cases WHERE id = %s", (case_id,)
            ).fetchone()
            if not case_row:
                raise HTTPException(status_code=404, detail="Mortality case not found")
        else:
            raise HTTPException(status_code=400, detail=f"Unknown case type: {case_type}")

        context = assemble_context(case_type, case_row, conn)

        review_row = conn.execute(
            "SELECT * FROM reviews WHERE case_type = %s AND case_id = %s",
            (case_type, case_id),
        ).fetchone()

    review = None
    if review_row:
        parsed = json.loads(review_row["structured_json"]) if review_row["structured_json"] else {}
        review = {
            "structured": parsed,
            "clinical_narrative": review_row["clinical_narrative"],
            "model_used": review_row["model_used"],
            "tokens_used": review_row["tokens_used"],
            "created_at": review_row["created_at"],
        }

    return {"context": context, "review": review}


@app.get("/api/reviews/{readmission_id}")
def get_review_detail(readmission_id: int):
    """Legacy endpoint — fetches readmission review by readmission_id."""
    pool = get_pool()
    with pool.connection() as conn:
        readmission_row = conn.execute(
            "SELECT * FROM readmissions WHERE id = %s", (readmission_id,)
        ).fetchone()
        if not readmission_row:
            raise HTTPException(status_code=404, detail="Readmission not found")

        context = assemble_context("readmission", readmission_row, conn)

        review_row = conn.execute(
            "SELECT * FROM reviews WHERE case_type = 'readmission' AND case_id = %s",
            (readmission_id,),
        ).fetchone()

    review = None
    if review_row:
        parsed = json.loads(review_row["structured_json"]) if review_row["structured_json"] else {}
        review = {
            "structured": parsed,
            "clinical_narrative": review_row["clinical_narrative"],
            "model_used": review_row["model_used"],
            "tokens_used": review_row["tokens_used"],
            "created_at": review_row["created_at"],
        }

    return {"context": context, "review": review}


# ── Analytics ────────────────────────────────────────────────────────────


@app.get("/api/analytics")
def get_analytics(
    case_type: str | None = Query(None, description="Filter by case type"),
):
    pool = get_pool()
    with pool.connection() as conn:
        reviews = _fetch_reviews(conn, case_type=case_type)
        factors = top_contributing_factors(reviews, top_n=15)
        interventions = top_recommended_interventions(reviews, top_n=15)
    return {
        "contributing_factors": [{"factor": f, "count": c} for f, c in factors],
        "recommended_interventions": [{"intervention": i, "count": c} for i, c in interventions],
    }


# ── Readmissions list ───────────────────────────────────────────────────


@app.get("/api/readmissions")
def get_readmissions(
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
):
    """All readmissions with patient info, encounter details, review status."""
    pool = get_pool()
    with pool.connection() as conn:
        total = conn.execute("SELECT COUNT(*) AS cnt FROM readmissions").fetchone()["cnt"]

        rows = conn.execute(
            """SELECT ra.id AS readmission_id,
                      ra.days_between,
                      ra.patient_id,
                      p.birth_date, p.gender, p.race, p.city, p.state,
                      ie.start AS index_start, ie."end" AS index_end,
                      ie.reason_display AS index_diagnosis,
                      ie.discharge_disposition,
                      re.start AS readmit_start, re."end" AS readmit_end,
                      re.reason_display AS readmit_diagnosis,
                      rv.id AS review_id,
                      rv.structured_json,
                      rv.created_at AS reviewed_at
               FROM readmissions ra
               JOIN patients p ON p.id = ra.patient_id
               JOIN encounters ie ON ie.id = ra.index_encounter_id
               JOIN encounters re ON re.id = ra.readmission_encounter_id
               LEFT JOIN reviews rv ON rv.case_type = 'readmission' AND rv.case_id = ra.id
               ORDER BY ra.id
               OFFSET %s LIMIT %s""",
            (offset, limit),
        ).fetchall()

    results = []
    for r in rows:
        parsed = {}
        if r["structured_json"]:
            parsed = json.loads(r["structured_json"])

        index_los = None
        if r["index_start"] and r["index_end"]:
            try:
                s = date.fromisoformat(r["index_start"][:10])
                e = date.fromisoformat(r["index_end"][:10])
                index_los = (e - s).days
            except (ValueError, TypeError):
                pass

        results.append({
            "readmission_id": r["readmission_id"],
            "patient_id": r["patient_id"],
            "age": _compute_age(r["birth_date"]),
            "gender": r["gender"],
            "race": r["race"],
            "city": r["city"],
            "state": r["state"],
            "days_between": r["days_between"],
            "index_start": r["index_start"],
            "index_end": r["index_end"],
            "index_length_of_stay": index_los,
            "index_diagnosis": r["index_diagnosis"] or "Unknown",
            "discharge_disposition": r["discharge_disposition"],
            "readmit_start": r["readmit_start"],
            "readmit_end": r["readmit_end"],
            "readmit_diagnosis": r["readmit_diagnosis"] or "Unknown",
            "review_status": "reviewed" if r["review_id"] is not None else "pending",
            "root_cause": parsed.get("root_cause_category"),
            "preventability_score": parsed.get("preventability_score"),
            "reviewed_at": r["reviewed_at"],
        })
    return {"total": total, "offset": offset, "limit": limit, "items": results}


@app.get("/api/readmissions/{readmission_id}")
def get_readmission_detail(readmission_id: int):
    """Single readmission detail — works for both reviewed and unreviewed."""
    pool = get_pool()
    with pool.connection() as conn:
        readmission_row = conn.execute(
            "SELECT * FROM readmissions WHERE id = %s", (readmission_id,)
        ).fetchone()
        if not readmission_row:
            raise HTTPException(status_code=404, detail="Readmission not found")

        context = assemble_context("readmission", readmission_row, conn)

        review_row = conn.execute(
            "SELECT * FROM reviews WHERE case_type = 'readmission' AND case_id = %s",
            (readmission_id,),
        ).fetchone()

    review = None
    if review_row:
        parsed = json.loads(review_row["structured_json"]) if review_row["structured_json"] else {}
        review = {
            "structured": parsed,
            "clinical_narrative": review_row["clinical_narrative"],
            "model_used": review_row["model_used"],
            "tokens_used": review_row["tokens_used"],
            "created_at": review_row["created_at"],
        }

    return {"context": context, "review": review}


# ── Mortality cases list ────────────────────────────────────────────────


@app.get("/api/mortality-cases")
def get_mortality_cases(
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
):
    """All mortality cases with patient info and review status."""
    pool = get_pool()
    with pool.connection() as conn:
        total = conn.execute("SELECT COUNT(*) AS cnt FROM mortality_cases").fetchone()["cnt"]

        rows = conn.execute(
            """SELECT mc.id AS case_id,
                      mc.encounter_id,
                      mc.patient_id,
                      mc.death_date,
                      mc.lookback_days,
                      p.birth_date, p.gender, p.race, p.city, p.state,
                      e.start AS encounter_start, e."end" AS encounter_end,
                      e.reason_display AS diagnosis,
                      e.discharge_disposition,
                      rv.id AS review_id,
                      rv.structured_json,
                      rv.created_at AS reviewed_at
               FROM mortality_cases mc
               JOIN patients p ON p.id = mc.patient_id
               JOIN encounters e ON e.id = mc.encounter_id
               LEFT JOIN reviews rv ON rv.case_type = 'mortality' AND rv.case_id = mc.id
               ORDER BY mc.id
               OFFSET %s LIMIT %s""",
            (offset, limit),
        ).fetchall()

    results = []
    for r in rows:
        parsed = {}
        if r["structured_json"]:
            parsed = json.loads(r["structured_json"])

        results.append({
            "case_id": r["case_id"],
            "encounter_id": r["encounter_id"],
            "patient_id": r["patient_id"],
            "death_date": r["death_date"],
            "lookback_days": r["lookback_days"],
            "age": _compute_age(r["birth_date"]),
            "gender": r["gender"],
            "race": r["race"],
            "city": r["city"],
            "state": r["state"],
            "encounter_start": r["encounter_start"],
            "encounter_end": r["encounter_end"],
            "diagnosis": r["diagnosis"] or "Unknown",
            "discharge_disposition": r["discharge_disposition"],
            "review_status": "reviewed" if r["review_id"] is not None else "pending",
            "root_cause": parsed.get("root_cause_category"),
            "preventability_score": parsed.get("preventability_score"),
            "reviewed_at": r["reviewed_at"],
        })
    return {"total": total, "offset": offset, "limit": limit, "items": results}


@app.get("/api/mortality-cases/{case_id}")
def get_mortality_case_detail(case_id: int):
    """Single mortality case detail with context and review."""
    pool = get_pool()
    with pool.connection() as conn:
        case_row = conn.execute(
            "SELECT * FROM mortality_cases WHERE id = %s", (case_id,)
        ).fetchone()
        if not case_row:
            raise HTTPException(status_code=404, detail="Mortality case not found")

        context = assemble_context("mortality", case_row, conn)

        review_row = conn.execute(
            "SELECT * FROM reviews WHERE case_type = 'mortality' AND case_id = %s",
            (case_id,),
        ).fetchone()

    review = None
    if review_row:
        parsed = json.loads(review_row["structured_json"]) if review_row["structured_json"] else {}
        review = {
            "structured": parsed,
            "clinical_narrative": review_row["clinical_narrative"],
            "model_used": review_row["model_used"],
            "tokens_used": review_row["tokens_used"],
            "created_at": review_row["created_at"],
        }

    return {"context": context, "review": review}


# ── Analytics dimensions ─────────────────────────────────────────────────


@app.get("/api/analytics/by-root-cause")
def analytics_by_root_cause(
    case_type: str | None = Query(None, description="Filter by case type"),
):
    """Reviews grouped by root_cause_category."""
    pool = get_pool()
    with pool.connection() as conn:
        reviews = _fetch_reviews(conn, case_type=case_type)
    groups: dict[str, list] = {}
    for r in reviews:
        cat = r["parsed"].get("root_cause_category", "Unknown")
        groups.setdefault(cat, []).append({
            "readmission_id": r["readmission_id"],
            "preventability_score": r["parsed"].get("preventability_score"),
        })
    results = []
    for k, v in sorted(groups.items(), key=lambda x: -len(x[1])):
        scores = [p["preventability_score"] for p in v if p["preventability_score"] is not None]
        avg = round(sum(scores) / len(scores), 2) if scores else None
        results.append({"category": k, "count": len(v), "avg_preventability": avg, "readmissions": v})
    return results


@app.get("/api/analytics/by-diagnosis")
def analytics_by_diagnosis(
    case_type: str | None = Query(None, description="Filter by case type"),
):
    """Reviews grouped by index encounter diagnosis — single query instead of N+1."""
    pool = get_pool()
    with pool.connection() as conn:
        base_where = "WHERE r.case_type = %s" if case_type else ""
        params: tuple = (case_type,) if case_type else ()

        rows = conn.execute(
            f"""SELECT r.case_type, r.case_id, r.readmission_id, r.structured_json,
                      (SELECT c.display FROM conditions c
                       WHERE c.encounter_id = COALESCE(ra.index_encounter_id, mc.encounter_id)
                       ORDER BY c.onset ASC LIMIT 1) AS diagnosis
               FROM reviews r
               LEFT JOIN readmissions ra ON r.case_type = 'readmission' AND ra.id = r.case_id
               LEFT JOIN mortality_cases mc ON r.case_type = 'mortality' AND mc.id = r.case_id
               {base_where}""",
            params,
        ).fetchall()

    groups: dict[str, list] = {}
    for r in rows:
        parsed = json.loads(r["structured_json"]) if r["structured_json"] else {}
        diag = r["diagnosis"] or "Unknown"
        groups.setdefault(diag, []).append({
            "readmission_id": r["readmission_id"] or r["case_id"],
            "root_cause": parsed.get("root_cause_category"),
            "preventability_score": parsed.get("preventability_score"),
        })
    return [
        {"diagnosis": k, "count": len(v), "readmissions": v}
        for k, v in sorted(groups.items(), key=lambda x: -len(x[1]))
    ]


@app.get("/api/analytics/by-gender")
def analytics_by_gender(
    case_type: str | None = Query(None, description="Filter by case type"),
):
    """Reviews grouped by patient gender — single query instead of N+1."""
    pool = get_pool()
    with pool.connection() as conn:
        base_where = "WHERE r.case_type = %s" if case_type else ""
        params: tuple = (case_type,) if case_type else ()

        rows = conn.execute(
            f"""SELECT r.case_type, r.case_id, r.readmission_id, r.structured_json, p.gender
               FROM reviews r
               LEFT JOIN readmissions ra ON r.case_type = 'readmission' AND ra.id = r.case_id
               LEFT JOIN mortality_cases mc ON r.case_type = 'mortality' AND mc.id = r.case_id
               JOIN patients p ON p.id = COALESCE(ra.patient_id, mc.patient_id)
               {base_where}""",
            params,
        ).fetchall()

    groups: dict[str, list] = {}
    for r in rows:
        parsed = json.loads(r["structured_json"]) if r["structured_json"] else {}
        gender = r["gender"] or "Unknown"
        groups.setdefault(gender, []).append({
            "readmission_id": r["readmission_id"] or r["case_id"],
            "root_cause": parsed.get("root_cause_category"),
            "preventability_score": parsed.get("preventability_score"),
        })
    return [
        {"gender": k, "count": len(v), "readmissions": v}
        for k, v in sorted(groups.items(), key=lambda x: -len(x[1]))
    ]


@app.get("/api/analytics/by-age-group")
def analytics_by_age_group():
    """All readmissions grouped by patient age bucket."""
    pool = get_pool()
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT ra.id AS readmission_id, ra.days_between, p.birth_date
               FROM readmissions ra
               JOIN patients p ON p.id = ra.patient_id"""
        ).fetchall()

    groups: dict[str, list] = {}
    for r in rows:
        age = _compute_age(r["birth_date"])
        if age is None:
            bucket = "Unknown"
        elif age < 50:
            bucket = "<50"
        elif age < 60:
            bucket = "50-59"
        elif age < 70:
            bucket = "60-69"
        elif age < 80:
            bucket = "70-79"
        elif age < 90:
            bucket = "80-89"
        else:
            bucket = "90+"
        groups.setdefault(bucket, []).append({
            "readmission_id": r["readmission_id"],
            "age": age,
            "days_between": r["days_between"],
        })
    return [
        {"age_group": k, "count": len(v), "readmissions": v}
        for k, v in sorted(groups.items())
    ]


@app.get("/api/analytics/by-days-between")
def analytics_by_days_between():
    """All readmissions grouped by days_between bucket."""
    pool = get_pool()
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT id AS readmission_id, days_between, patient_id
               FROM readmissions
               ORDER BY days_between"""
        ).fetchall()

    groups: dict[str, list] = {}
    for r in rows:
        d = r["days_between"]
        if d <= 5:
            bucket = "0-5"
        elif d <= 10:
            bucket = "6-10"
        elif d <= 15:
            bucket = "11-15"
        elif d <= 20:
            bucket = "16-20"
        elif d <= 25:
            bucket = "21-25"
        else:
            bucket = "26-30"
        groups.setdefault(bucket, []).append({
            "readmission_id": r["readmission_id"],
            "days_between": d,
        })

    order = ["0-5", "6-10", "11-15", "16-20", "21-25", "26-30"]
    return [
        {"bucket": b, "count": len(groups.get(b, [])), "readmissions": groups.get(b, [])}
        for b in order if b in groups
    ]


# ── Background review worker ─────────────────────────────────────────────


def _prune_old_jobs() -> None:
    """Remove completed/failed jobs older than 1 hour, enforce max size. Must hold _jobs_lock."""
    cutoff = time.monotonic() - 3600
    expired = [
        jid for jid, j in _jobs.items()
        if j["status"] in (JobStatus.COMPLETED, JobStatus.FAILED) and j["finished_at"] and j["finished_at"] < cutoff
    ]
    for jid in expired:
        _jobs.pop(jid, None)

    # Hard cap: evict oldest finished jobs if over limit
    if len(_jobs) > _MAX_JOBS:
        finished = sorted(
            ((jid, j) for jid, j in _jobs.items() if j["status"] in (JobStatus.COMPLETED, JobStatus.FAILED)),
            key=lambda x: x[1].get("finished_at") or 0,
        )
        to_remove = len(_jobs) - _MAX_JOBS
        for jid, _ in finished[:to_remove]:
            _jobs.pop(jid, None)


def _run_review_background(job_id: str, case_type: str, case_id: int) -> None:
    """Worker function that runs in a thread pool."""
    from clinical_review_agent.agent.context import context_to_prompt_string
    from clinical_review_agent.agent.reviewer import _call_claude, _parse_response, _store_review, USER_PROMPT_PREFIXES

    with _jobs_lock:
        _jobs[job_id]["status"] = JobStatus.RUNNING

    try:
        pool = get_pool()

        # Look up the case row from the right table
        if case_type == "readmission":
            with pool.connection() as conn:
                case_row = conn.execute(
                    "SELECT * FROM readmissions WHERE id = %s", (case_id,)
                ).fetchone()
                if not case_row:
                    raise ValueError(f"Readmission {case_id} not found")
                context = assemble_context("readmission", case_row, conn)
        elif case_type == "mortality":
            with pool.connection() as conn:
                case_row = conn.execute(
                    "SELECT * FROM mortality_cases WHERE id = %s", (case_id,)
                ).fetchone()
                if not case_row:
                    raise ValueError(f"Mortality case {case_id} not found")
                context = assemble_context("mortality", case_row, conn)
        else:
            raise ValueError(f"Unknown case type: {case_type}")

        prompt_str = context_to_prompt_string(context)
        user_prefix = USER_PROMPT_PREFIXES.get(case_type, USER_PROMPT_PREFIXES["readmission"])
        user_prompt = (
            f"{user_prefix} "
            "The clinical data is provided as structured JSON.\n\n"
            f"{prompt_str}"
        )

        response_text, tokens_used = _call_claude(user_prompt, case_type=case_type)
        structured, narrative = _parse_response(response_text)

        with pool.connection() as conn:
            conn.autocommit = False
            try:
                _store_review(conn, case_id, structured, narrative, tokens_used, case_type=case_type)
            finally:
                conn.autocommit = True

        with _jobs_lock:
            _jobs[job_id]["status"] = JobStatus.COMPLETED
            _jobs[job_id]["finished_at"] = time.monotonic()
            _jobs[job_id]["result"] = {
                "root_cause_category": structured.get("root_cause_category"),
                "preventability_score": structured.get("preventability_score"),
                "tokens_used": tokens_used,
            }
        logger.info(
            "Review completed for %s %d", case_type, case_id,
            extra={"case_type": case_type, "case_id": case_id},
        )

    except Exception as exc:
        logger.exception("Background review failed for %s %d", case_type, case_id)
        with _jobs_lock:
            _jobs[job_id]["status"] = JobStatus.FAILED
            _jobs[job_id]["finished_at"] = time.monotonic()
            _jobs[job_id]["error"] = str(exc)

    finally:
        with _jobs_lock:
            _active_reviews.pop((case_type, case_id), None)


# ── Trigger review (generalized) ────────────────────────────────────────


@app.post("/api/reviews/{case_type}/{case_id}/run", status_code=202)
def trigger_review_typed(case_type: str, case_id: int):
    """Submit a background AI review for a case of any type."""
    if case_type not in ("readmission", "mortality"):
        raise HTTPException(status_code=400, detail=f"Unknown case type: {case_type}")

    if not _review_limiter.check():
        raise HTTPException(
            status_code=429,
            detail="Review rate limit exceeded. Try again in a minute.",
        )

    pool = get_pool()
    with pool.connection() as conn:
        if case_type == "readmission":
            row = conn.execute("SELECT id FROM readmissions WHERE id = %s", (case_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Readmission not found")
        elif case_type == "mortality":
            row = conn.execute("SELECT id FROM mortality_cases WHERE id = %s", (case_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Mortality case not found")

    review_key = (case_type, case_id)

    with _jobs_lock:
        if review_key in _active_reviews:
            existing_job_id = _active_reviews[review_key]
            raise HTTPException(
                status_code=409,
                detail={
                    "job_id": existing_job_id,
                    "message": f"A review is already running for this {case_type}.",
                },
            )

        _prune_old_jobs()

        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {
            "job_id": job_id,
            "case_type": case_type,
            "case_id": case_id,
            "readmission_id": case_id if case_type == "readmission" else None,
            "status": JobStatus.PENDING,
            "result": None,
            "error": None,
            "created_at": time.monotonic(),
            "finished_at": None,
        }
        _active_reviews[review_key] = job_id

    if _executor is None:
        raise HTTPException(status_code=503, detail="Server not fully started")

    _executor.submit(_run_review_background, job_id, case_type, case_id)
    return {"job_id": job_id, "status": "pending", "case_type": case_type, "case_id": case_id}


@app.post("/api/reviews/{readmission_id}/run", status_code=202)
def trigger_review(readmission_id: int):
    """Legacy endpoint — submit a background AI review for a readmission."""
    return trigger_review_typed("readmission", readmission_id)


# ── Job polling ──────────────────────────────────────────────────────────


@app.get("/api/jobs")
def list_jobs():
    """List all tracked jobs (active and recently finished)."""
    with _jobs_lock:
        snapshot = [
            {
                "job_id": j["job_id"],
                "case_type": j.get("case_type", "readmission"),
                "case_id": j.get("case_id"),
                "readmission_id": j.get("readmission_id"),
                "status": j["status"],
                "error": j["error"],
            }
            for j in _jobs.values()
        ]
    return {"jobs": snapshot}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """Poll the status of a background review job."""
    with _jobs_lock:
        job = dict(_jobs[job_id]) if job_id in _jobs else None
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job["job_id"],
        "case_type": job.get("case_type", "readmission"),
        "case_id": job.get("case_id"),
        "readmission_id": job.get("readmission_id"),
        "status": job["status"],
        "result": job["result"],
        "error": job["error"],
    }


if __name__ == "__main__":
    import uvicorn

    cfg = get_settings()
    uvicorn.run(app, host=cfg.server_host, port=cfg.server_port)
