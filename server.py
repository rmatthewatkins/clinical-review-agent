"""FastAPI backend for the readmissions review agent."""

import json
from datetime import date
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import threading

import psycopg
from psycopg.rows import dict_row

from src.schema import DATABASE_URL
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

_local = threading.local()


def get_db() -> psycopg.Connection:
    """Return a thread-local connection (one per worker thread)."""
    conn = getattr(_local, "conn", None)
    if conn is None or conn.closed:
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True, connect_timeout=10)
        _local.conn = conn
    return conn


app = FastAPI(title="Readmissions Review Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET", "POST"],
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


# ── Pairs list ───────────────────────────────────────────────────────────


@app.get("/api/pairs")
def get_pairs():
    """All readmission pairs with patient info, encounter details, review status."""
    conn = get_db()
    rows = conn.execute(
        """SELECT rp.id AS pair_id,
                  rp.days_between,
                  rp.patient_id,
                  p.birth_date, p.gender, p.race, p.city, p.state,
                  ie.start AS index_start, ie."end" AS index_end,
                  ie.reason_display AS index_diagnosis,
                  ie.discharge_disposition,
                  re.start AS readmit_start, re."end" AS readmit_end,
                  re.reason_display AS readmit_diagnosis,
                  rv.id AS review_id,
                  rv.structured_json,
                  rv.created_at AS reviewed_at
           FROM readmission_pairs rp
           JOIN patients p ON p.id = rp.patient_id
           JOIN encounters ie ON ie.id = rp.index_encounter_id
           JOIN encounters re ON re.id = rp.readmission_encounter_id
           LEFT JOIN reviews rv ON rv.pair_id = rp.id
           ORDER BY rp.id"""
    ).fetchall()

    results = []
    for r in rows:
        parsed = {}
        if r["structured_json"]:
            parsed = json.loads(r["structured_json"])

        # Compute index length of stay in days
        index_los = None
        if r["index_start"] and r["index_end"]:
            try:
                s = date.fromisoformat(r["index_start"][:10])
                e = date.fromisoformat(r["index_end"][:10])
                index_los = (e - s).days
            except (ValueError, TypeError):
                pass

        results.append({
            "pair_id": r["pair_id"],
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
    return results


@app.get("/api/pairs/{pair_id}")
def get_pair_detail(pair_id: int):
    """Single pair detail — works for both reviewed and unreviewed pairs."""
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


# ── Analytics dimensions ─────────────────────────────────────────────────


@app.get("/api/analytics/by-root-cause")
def analytics_by_root_cause():
    """Reviews grouped by root_cause_category."""
    conn = get_db()
    reviews = _fetch_reviews(conn)
    groups: dict[str, list] = {}
    for r in reviews:
        cat = r["parsed"].get("root_cause_category", "Unknown")
        groups.setdefault(cat, []).append({
            "pair_id": r["pair_id"],
            "preventability_score": r["parsed"].get("preventability_score"),
        })
    results = []
    for k, v in sorted(groups.items(), key=lambda x: -len(x[1])):
        scores = [p["preventability_score"] for p in v if p["preventability_score"] is not None]
        avg = round(sum(scores) / len(scores), 2) if scores else None
        results.append({"category": k, "count": len(v), "avg_preventability": avg, "pairs": v})
    return results


@app.get("/api/analytics/by-diagnosis")
def analytics_by_diagnosis():
    """Reviews grouped by index encounter diagnosis."""
    conn = get_db()
    reviews = _fetch_reviews(conn)
    groups: dict[str, list] = {}
    for r in reviews:
        diag = _fetch_diagnosis_for_pair(conn, r["pair_id"]) or "Unknown"
        groups.setdefault(diag, []).append({
            "pair_id": r["pair_id"],
            "root_cause": r["parsed"].get("root_cause_category"),
            "preventability_score": r["parsed"].get("preventability_score"),
        })
    return [
        {"diagnosis": k, "count": len(v), "pairs": v}
        for k, v in sorted(groups.items(), key=lambda x: -len(x[1]))
    ]


@app.get("/api/analytics/by-gender")
def analytics_by_gender():
    """Reviews grouped by patient gender."""
    conn = get_db()
    reviews = _fetch_reviews(conn)
    groups: dict[str, list] = {}
    for r in reviews:
        pair_row = conn.execute(
            """SELECT p.gender FROM readmission_pairs rp
               JOIN patients p ON p.id = rp.patient_id
               WHERE rp.id = %s""",
            (r["pair_id"],),
        ).fetchone()
        gender = pair_row["gender"] if pair_row else "Unknown"
        groups.setdefault(gender, []).append({
            "pair_id": r["pair_id"],
            "root_cause": r["parsed"].get("root_cause_category"),
            "preventability_score": r["parsed"].get("preventability_score"),
        })
    return [
        {"gender": k, "count": len(v), "pairs": v}
        for k, v in sorted(groups.items(), key=lambda x: -len(x[1]))
    ]


@app.get("/api/analytics/by-age-group")
def analytics_by_age_group():
    """All pairs grouped by patient age bucket."""
    conn = get_db()
    rows = conn.execute(
        """SELECT rp.id AS pair_id, rp.days_between, p.birth_date
           FROM readmission_pairs rp
           JOIN patients p ON p.id = rp.patient_id"""
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
            "pair_id": r["pair_id"],
            "age": age,
            "days_between": r["days_between"],
        })
    return [
        {"age_group": k, "count": len(v), "pairs": v}
        for k, v in sorted(groups.items())
    ]


@app.get("/api/analytics/by-days-between")
def analytics_by_days_between():
    """All pairs grouped by days_between bucket."""
    conn = get_db()
    rows = conn.execute(
        """SELECT id AS pair_id, days_between, patient_id
           FROM readmission_pairs
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
            "pair_id": r["pair_id"],
            "days_between": d,
        })

    order = ["0-5", "6-10", "11-15", "16-20", "21-25", "26-30"]
    return [
        {"bucket": b, "count": len(groups.get(b, [])), "pairs": groups.get(b, [])}
        for b in order if b in groups
    ]


# ── Trigger review ───────────────────────────────────────────────────────


@app.post("/api/reviews/{pair_id}/run")
def trigger_review(pair_id: int):
    """Run an AI review for a single readmission pair."""
    from src.agent.context import context_to_prompt_string
    from src.agent.reviewer import _call_claude, _parse_response, _store_review

    conn = get_db()

    pair_row = conn.execute(
        "SELECT * FROM readmission_pairs WHERE id = %s", (pair_id,)
    ).fetchone()
    if not pair_row:
        raise HTTPException(status_code=404, detail="Pair not found")

    # Check if already reviewed
    existing = conn.execute(
        "SELECT id FROM reviews WHERE pair_id = %s", (pair_id,)
    ).fetchone()

    context = assemble_context(pair_row, conn)
    prompt_str = context_to_prompt_string(context)
    user_prompt = (
        "Please review the following 30-day hospital readmission case. "
        "The clinical data is provided as structured JSON.\n\n"
        f"{prompt_str}"
    )

    # Need a non-autocommit connection for _store_review (it calls conn.commit())
    import psycopg
    from psycopg.rows import dict_row as dr
    write_conn = psycopg.connect(DATABASE_URL, row_factory=dr, connect_timeout=10)
    try:
        response_text, tokens_used = _call_claude(user_prompt)
        structured, narrative = _parse_response(response_text)
        _store_review(write_conn, pair_id, structured, narrative, tokens_used)
    finally:
        write_conn.close()

    return {
        "pair_id": pair_id,
        "was_update": existing is not None,
        "root_cause_category": structured.get("root_cause_category"),
        "preventability_score": structured.get("preventability_score"),
        "tokens_used": tokens_used,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
