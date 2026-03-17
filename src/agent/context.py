"""Context assembler for readmission pair clinical review.

Given a readmission_pair row, queries the PostgreSQL database and assembles
a comprehensive clinical context dict for LLM-based peer review.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any


def _rows_to_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure rows are plain dicts (no-op with psycopg dict_row, but safe)."""
    return [dict(r) for r in rows]


def _compute_age(birth_date_str: str | None, reference_date_str: str | None = None) -> int | None:
    """Compute age in years from birth_date string to a reference date."""
    if not birth_date_str:
        return None
    try:
        bd = date.fromisoformat(birth_date_str)
    except (ValueError, TypeError):
        return None
    if reference_date_str:
        try:
            ref = date.fromisoformat(reference_date_str[:10])
        except (ValueError, TypeError):
            ref = date.today()
    else:
        ref = date.today()
    age = ref.year - bd.year - ((ref.month, ref.day) < (bd.month, bd.day))
    return age


def assemble_context(pair: dict[str, Any], conn) -> dict[str, Any]:
    """Assemble a full clinical context dict for a readmission pair.

    Parameters
    ----------
    pair : dict
        A row from readmission_pairs with keys: id, index_encounter_id,
        readmission_encounter_id, days_between, patient_id.
    conn : psycopg.Connection
        Database connection (must use dict_row factory).

    Returns
    -------
    dict
        Structured clinical context suitable for serialization to an LLM prompt.
    """
    pair = dict(pair)
    patient_id = pair["patient_id"]
    index_enc_id = pair["index_encounter_id"]
    readmit_enc_id = pair["readmission_encounter_id"]
    cur = conn.cursor()

    # ── Patient baseline ────────────────────────────────────────────────
    cur.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
    patient_row = cur.fetchone()
    patient = dict(patient_row) if patient_row else {}

    # Index encounter (need dates for age calc and interval)
    cur.execute("SELECT * FROM encounters WHERE id = %s", (index_enc_id,))
    index_enc = dict(cur.fetchone() or {})

    # Readmission encounter
    cur.execute("SELECT * FROM encounters WHERE id = %s", (readmit_enc_id,))
    readmit_enc = dict(cur.fetchone() or {})

    index_start = index_enc.get("start")
    index_end = index_enc.get("end")
    readmit_start = readmit_enc.get("start")

    age = _compute_age(patient.get("birth_date"), index_start)

    # Chronic conditions: no abatement date or clinical_status = active
    cur.execute(
        """SELECT DISTINCT code, display, onset, clinical_status
           FROM conditions
           WHERE patient_id = %s
             AND (abatement IS NULL OR abatement = '' OR clinical_status = 'active')
           ORDER BY onset""",
        (patient_id,),
    )
    chronic_conditions = _rows_to_dicts(cur.fetchall())

    # Active medications at time of index admission
    cur.execute(
        """SELECT DISTINCT code, display, start, "end", status
           FROM medications
           WHERE patient_id = %s
             AND (status = 'active' OR "end" IS NULL OR "end" = '' OR "end" >= %s)
           ORDER BY start""",
        (patient_id, index_start or ""),
    )
    active_medications = _rows_to_dicts(cur.fetchall())

    # Relevant observations (most recent before index admission)
    cur.execute(
        """SELECT display, value, unit, date
           FROM observations
           WHERE patient_id = %s
             AND date <= %s
           ORDER BY date DESC
           LIMIT 50""",
        (patient_id, index_start or "9999-12-31"),
    )
    recent_observations = _rows_to_dicts(cur.fetchall())

    patient_baseline = {
        "patient_id": patient_id,
        "age": age,
        "gender": patient.get("gender"),
        "race": patient.get("race"),
        "ethnicity": patient.get("ethnicity"),
        "city": patient.get("city"),
        "state": patient.get("state"),
        "chronic_conditions": chronic_conditions,
        "active_medications": active_medications,
        "relevant_observations": recent_observations,
    }

    # ── Index admission ─────────────────────────────────────────────────
    # Length of stay
    los = None
    if index_start and index_end:
        try:
            d1 = datetime.fromisoformat(index_start)
            d2 = datetime.fromisoformat(index_end)
            los = (d2 - d1).days
        except (ValueError, TypeError):
            pass

    # Diagnoses during index encounter
    cur.execute(
        "SELECT code, display, onset, clinical_status FROM conditions WHERE encounter_id = %s ORDER BY onset",
        (index_enc_id,),
    )
    index_diagnoses = _rows_to_dicts(cur.fetchall())

    # Procedures during index encounter
    cur.execute(
        "SELECT code, display, date FROM procedures WHERE encounter_id = %s ORDER BY date",
        (index_enc_id,),
    )
    index_procedures = _rows_to_dicts(cur.fetchall())

    # Medications prescribed during index encounter
    cur.execute(
        """SELECT code, display, start, "end", status FROM medications WHERE encounter_id = %s ORDER BY start""",
        (index_enc_id,),
    )
    index_medications = _rows_to_dicts(cur.fetchall())

    # Observations during index encounter
    cur.execute(
        "SELECT display, value, unit, date FROM observations WHERE encounter_id = %s ORDER BY date",
        (index_enc_id,),
    )
    index_observations = _rows_to_dicts(cur.fetchall())

    index_admission = {
        "encounter_id": index_enc_id,
        "start": index_start,
        "end": index_end,
        "length_of_stay_days": los,
        "type": index_enc.get("type"),
        "reason_code": index_enc.get("reason_code"),
        "reason_display": index_enc.get("reason_display"),
        "discharge_disposition": index_enc.get("discharge_disposition"),
        "diagnoses": index_diagnoses,
        "procedures": index_procedures,
        "medications": index_medications,
        "observations": index_observations,
    }

    # ── Interval care ───────────────────────────────────────────────────
    # Encounters between index discharge and readmission admission
    interval_encounters: list[dict] = []
    if index_end and readmit_start:
        cur.execute(
            """SELECT * FROM encounters
               WHERE patient_id = %s
                 AND id != %s AND id != %s
                 AND start >= %s AND start < %s
               ORDER BY start""",
            (patient_id, index_enc_id, readmit_enc_id, index_end, readmit_start),
        )
        interval_encounters = _rows_to_dicts(cur.fetchall())

    # Medication changes in the interval
    new_meds_started: list[dict] = []
    stopped_meds: list[dict] = []
    if index_end and readmit_start:
        cur.execute(
            """SELECT code, display, start, "end", status
               FROM medications
               WHERE patient_id = %s
                 AND start >= %s AND start < %s
               ORDER BY start""",
            (patient_id, index_end, readmit_start),
        )
        new_meds_started = _rows_to_dicts(cur.fetchall())

        cur.execute(
            """SELECT code, display, start, "end", status
               FROM medications
               WHERE patient_id = %s
                 AND "end" >= %s AND "end" < %s
                 AND (status = 'stopped' OR status = 'completed')
               ORDER BY "end" """,
            (patient_id, index_end, readmit_start),
        )
        stopped_meds = _rows_to_dicts(cur.fetchall())

    # New conditions diagnosed in interval
    new_conditions: list[dict] = []
    if index_end and readmit_start:
        cur.execute(
            """SELECT code, display, onset, clinical_status
               FROM conditions
               WHERE patient_id = %s
                 AND onset >= %s AND onset < %s
               ORDER BY onset""",
            (patient_id, index_end, readmit_start),
        )
        new_conditions = _rows_to_dicts(cur.fetchall())

    interval_care = {
        "encounters": interval_encounters,
        "new_medications_started": new_meds_started,
        "stopped_medications": stopped_meds,
        "new_conditions_diagnosed": new_conditions,
    }

    # ── Readmission ─────────────────────────────────────────────────────
    cur.execute(
        "SELECT code, display, onset, clinical_status FROM conditions WHERE encounter_id = %s ORDER BY onset",
        (readmit_enc_id,),
    )
    readmit_diagnoses = _rows_to_dicts(cur.fetchall())

    cur.execute(
        "SELECT display, value, unit, date FROM observations WHERE encounter_id = %s ORDER BY date",
        (readmit_enc_id,),
    )
    readmit_observations = _rows_to_dicts(cur.fetchall())

    readmission = {
        "encounter_id": readmit_enc_id,
        "start": readmit_start,
        "end": readmit_enc.get("end"),
        "type": readmit_enc.get("type"),
        "reason_code": readmit_enc.get("reason_code"),
        "reason_display": readmit_enc.get("reason_display"),
        "diagnoses": readmit_diagnoses,
        "observations": readmit_observations,
    }

    # ── Assemble full context ───────────────────────────────────────────
    context: dict[str, Any] = {
        "pair_id": pair["id"],
        "days_between": pair["days_between"],
        "patient_baseline": patient_baseline,
        "index_admission": index_admission,
        "interval_care": interval_care,
        "readmission": readmission,
    }
    return context


def context_to_prompt_string(context: dict[str, Any]) -> str:
    """Serialize a context dict to a human-readable string for an LLM prompt."""
    return json.dumps(context, indent=2, default=str)
