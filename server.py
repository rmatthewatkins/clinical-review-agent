"""FastAPI backend for the readmissions review agent."""

import json
from datetime import date
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.schema import get_connection
from src.analytics.analyze import (
    _fetch_reviews,
    _fetch_diagnosis_for_pair,
    root_cause_distribution,
    preventability_score_distribution,
    mean_preventability_by_diagnosis,
    top_contributing_factors,
    top_recommended_interventions,
)
from src.agent.context import assemble_context


def get_db():
    """Create a new PostgreSQL connection per call."""
    return get_connection()


app = FastAPI(title="Readmissions Review Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


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
    conn = get_db()
    patients = conn.execute("SELECT COUNT(*) AS cnt FROM patients").fetchone()["cnt"]
    encounters = conn.execute("SELECT COUNT(*) AS cnt FROM encounters").fetchone()["cnt"]
    pairs = conn.execute("SELECT COUNT(*) AS cnt FROM readmission_pairs").fetchone()["cnt"]
    review_count = conn.execute("SELECT COUNT(*) AS cnt FROM reviews").fetchone()["cnt"]

    reviews = _fetch_reviews(conn)
    rc = root_cause_distribution(reviews)
    sd = preventability_score_distribution(reviews)
    mpd = mean_preventability_by_diagnosis(conn, reviews)
    top_10_mpd = dict(list(mpd.items())[:10])

    return {
        "patients": patients,
        "encounters": encounters,
        "pairs": pairs,
        "reviews": review_count,
        "root_cause_distribution": rc,
        "preventability_score_distribution": sd,
        "mean_preventability_by_diagnosis": top_10_mpd,
    }


# ── Reviews list ─────────────────────────────────────────────────────────


@app.get("/api/reviews")
def get_reviews():
    conn = get_db()
    reviews = _fetch_reviews(conn)
    results = []
    for r in reviews:
        parsed = r["parsed"]
        pair_row = conn.execute(
            """SELECT rp.id, rp.days_between, p.birth_date, p.gender
               FROM readmission_pairs rp
               JOIN patients p ON p.id = rp.patient_id
               WHERE rp.id = %s""",
            (r["pair_id"],),
        ).fetchone()

        age = None
        gender = None
        if pair_row:
            age = _compute_age(pair_row["birth_date"])
            gender = pair_row["gender"]

        diagnosis = _fetch_diagnosis_for_pair(conn, r["pair_id"])
        results.append({
            "pair_id": r["pair_id"],
            "age": age,
            "gender": gender,
            "index_diagnosis": diagnosis or "Unknown",
            "root_cause": parsed.get("root_cause_category", "Unknown"),
            "preventability_score": parsed.get("preventability_score"),
            "confidence": parsed.get("confidence_level"),
            "reviewed_at": r["created_at"],
        })
    return results


# ── Single review detail ─────────────────────────────────────────────────


@app.get("/api/reviews/{pair_id}")
def get_review_detail(pair_id: int):
    conn = get_db()

    pair_row = conn.execute(
        "SELECT * FROM readmission_pairs WHERE id = %s", (pair_id,)
    ).fetchone()
    if not pair_row:
        raise HTTPException(status_code=404, detail="Pair not found")

    context = assemble_context(pair_row, conn)

    review_row = conn.execute(
        "SELECT * FROM reviews WHERE pair_id = %s", (pair_id,)
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
def get_analytics():
    conn = get_db()
    reviews = _fetch_reviews(conn)
    factors = top_contributing_factors(reviews, top_n=15)
    interventions = top_recommended_interventions(reviews, top_n=15)
    return {
        "contributing_factors": [{"factor": f, "count": c} for f, c in factors],
        "recommended_interventions": [{"intervention": i, "count": c} for i, c in interventions],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
