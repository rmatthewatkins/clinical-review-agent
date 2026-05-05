"""Context assemblers for clinical case review.

Given a case row (readmission or mortality), queries the PostgreSQL database
and assembles a comprehensive clinical context dict for LLM-based peer review.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any


def _rows_to_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure rows are plain dicts (no-op with psycopg dict_row, but safe)."""
    return [dict(r) for r in rows]


# Note categories whose full text we always include in the prompt — these
# are the clinically definitive documents a peer reviewer would read first.
_KEY_NOTE_CATEGORIES = {"discharge summary"}

# Note categories whose full text we include for PSI review specifically.
# Radiology reports are the canonical place where PSI exclusions hide
# (e.g. an undiagnosed pleural effusion that should exclude PSI 06).
_PSI_KEY_NOTE_CATEGORIES = {"discharge summary", "radiology"}

# Physician-note descriptions that signal an admission narrative (HPI, PMH,
# exam, A/P) — the closest MIMIC analog to an H&P. Matched case-insensitively
# as substrings of the description.
_KEY_PHYSICIAN_DESC_PATTERNS = (
    "h&p",
    "h & p",
    "history and physical",
    "admission note",
    "admit note",
    "initial",
)


def _is_key_note(category: str | None, description: str | None) -> bool:
    """Return True if this note's full text should be included in the prompt."""
    cat = (category or "").strip().lower()
    if cat in _KEY_NOTE_CATEGORIES:
        return True
    if cat == "physician":
        desc = (description or "").strip().lower()
        return any(p in desc for p in _KEY_PHYSICIAN_DESC_PATTERNS)
    return False


def _is_psi_key_note(category: str | None, description: str | None) -> bool:
    """Return True if this note's full text should be included in a PSI prompt.

    PSI review needs radiology reports (and discharge summaries) — they
    are where uncoded exclusions hide.
    """
    cat = (category or "").strip().lower()
    return cat in _PSI_KEY_NOTE_CATEGORIES


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


# ── Shared query helpers ──────────────────────────────────────────────


def _build_patient_baseline(
    patient_id: str, reference_date: str | None, conn
) -> dict[str, Any]:
    """Build patient demographics, chronic conditions, active meds, recent observations."""
    cur = conn.cursor()

    cur.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
    patient_row = cur.fetchone()
    patient = dict(patient_row) if patient_row else {}

    age = _compute_age(patient.get("birth_date"), reference_date)

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

    # Active medications at reference date
    cur.execute(
        """SELECT DISTINCT code, display, start, "end", status
           FROM medications
           WHERE patient_id = %s
             AND (status = 'active' OR "end" IS NULL OR "end" = '' OR "end" >= %s)
           ORDER BY start""",
        (patient_id, reference_date or ""),
    )
    active_medications = _rows_to_dicts(cur.fetchall())

    # Relevant observations (most recent before reference date)
    cur.execute(
        """SELECT display, value, unit, date
           FROM observations
           WHERE patient_id = %s
             AND date <= %s
           ORDER BY date DESC
           LIMIT 50""",
        (patient_id, reference_date or "9999-12-31"),
    )
    recent_observations = _rows_to_dicts(cur.fetchall())

    return {
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


def _build_encounter_details(encounter_id: str, conn) -> dict[str, Any]:
    """Build full details for a single encounter: diagnoses, procedures, medications, observations."""
    cur = conn.cursor()

    cur.execute("SELECT * FROM encounters WHERE id = %s", (encounter_id,))
    enc = dict(cur.fetchone() or {})

    enc_start = enc.get("start")
    enc_end = enc.get("end")

    # Length of stay
    los = None
    if enc_start and enc_end:
        try:
            d1 = datetime.fromisoformat(enc_start)
            d2 = datetime.fromisoformat(enc_end)
            los = (d2 - d1).days
        except (ValueError, TypeError):
            pass

    cur.execute(
        "SELECT code, display, onset, clinical_status FROM conditions WHERE encounter_id = %s ORDER BY onset",
        (encounter_id,),
    )
    diagnoses = _rows_to_dicts(cur.fetchall())

    cur.execute(
        "SELECT code, display, date FROM procedures WHERE encounter_id = %s ORDER BY date",
        (encounter_id,),
    )
    procedures = _rows_to_dicts(cur.fetchall())

    cur.execute(
        """SELECT code, display, start, "end", status FROM medications WHERE encounter_id = %s ORDER BY start""",
        (encounter_id,),
    )
    medications = _rows_to_dicts(cur.fetchall())

    cur.execute(
        "SELECT display, value, unit, date FROM observations WHERE encounter_id = %s ORDER BY date",
        (encounter_id,),
    )
    observations = _rows_to_dicts(cur.fetchall())

    # Notes — split into full-text "key_notes" (discharge summary + admission
    # H&P) and a metadata-only "notes_index" so the model knows what other
    # documents exist without us paying tokens to render their full text.
    cur.execute(
        """SELECT category, description, chartdate, charttime, text
           FROM notes
           WHERE encounter_id = %s
           ORDER BY COALESCE(charttime, chartdate)""",
        (encounter_id,),
    )
    note_rows = _rows_to_dicts(cur.fetchall())

    key_notes: list[dict] = []
    notes_index: list[dict] = []
    for n in note_rows:
        notes_index.append({
            "category": n.get("category"),
            "description": n.get("description"),
            "chartdate": n.get("chartdate"),
            "charttime": n.get("charttime"),
        })
        if _is_key_note(n.get("category"), n.get("description")):
            key_notes.append({
                "category": n.get("category"),
                "description": n.get("description"),
                "chartdate": n.get("chartdate"),
                "charttime": n.get("charttime"),
                "text": n.get("text"),
            })

    return {
        "encounter_id": encounter_id,
        "start": enc_start,
        "end": enc_end,
        "length_of_stay_days": los,
        "type": enc.get("type"),
        "reason_code": enc.get("reason_code"),
        "reason_display": enc.get("reason_display"),
        "discharge_disposition": enc.get("discharge_disposition"),
        "diagnoses": diagnoses,
        "procedures": procedures,
        "medications": medications,
        "observations": observations,
        "key_notes": key_notes,
        "notes_index": notes_index,
    }


# ── Readmission context ──────────────────────────────────────────────


def _assemble_readmission_context(readmission_row: dict[str, Any], conn) -> dict[str, Any]:
    """Assemble a full clinical context dict for a readmission."""
    readmission_row = dict(readmission_row)
    patient_id = readmission_row["patient_id"]
    index_enc_id = readmission_row["index_encounter_id"]
    readmit_enc_id = readmission_row["readmission_encounter_id"]
    cur = conn.cursor()

    # Get encounter dates for interval queries
    cur.execute("SELECT * FROM encounters WHERE id = %s", (index_enc_id,))
    index_enc = dict(cur.fetchone() or {})
    cur.execute("SELECT * FROM encounters WHERE id = %s", (readmit_enc_id,))
    readmit_enc = dict(cur.fetchone() or {})

    index_start = index_enc.get("start")
    index_end = index_enc.get("end")
    readmit_start = readmit_enc.get("start")

    patient_baseline = _build_patient_baseline(patient_id, index_start, conn)
    index_admission = _build_encounter_details(index_enc_id, conn)

    # ── Interval care ────────────────────────────────────────────────
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

    # ── Readmission ──────────────────────────────────────────────────
    readmission_details = _build_encounter_details(readmit_enc_id, conn)

    context: dict[str, Any] = {
        "case_type": "readmission",
        "case_id": readmission_row["id"],
        "readmission_id": readmission_row["id"],
        "days_between": readmission_row["days_between"],
        "patient_baseline": patient_baseline,
        "index_admission": index_admission,
        "interval_care": interval_care,
        "readmission": {
            "encounter_id": readmission_details["encounter_id"],
            "start": readmission_details["start"],
            "end": readmission_details["end"],
            "type": readmission_details["type"],
            "reason_code": readmission_details["reason_code"],
            "reason_display": readmission_details["reason_display"],
            "diagnoses": readmission_details["diagnoses"],
            "observations": readmission_details["observations"],
            "key_notes": readmission_details["key_notes"],
            "notes_index": readmission_details["notes_index"],
        },
    }
    return context


# ── Mortality context ─────────────────────────────────────────────────


def _assemble_mortality_context(case_row: dict[str, Any], conn) -> dict[str, Any]:
    """Assemble a full clinical context dict for a mortality case."""
    case_row = dict(case_row)
    patient_id = case_row["patient_id"]
    encounter_id = case_row["encounter_id"]
    lookback_days = case_row.get("lookback_days", 90)
    cur = conn.cursor()

    # Get the death encounter to determine reference dates
    cur.execute("SELECT * FROM encounters WHERE id = %s", (encounter_id,))
    death_enc = dict(cur.fetchone() or {})
    enc_start = death_enc.get("start")

    patient_baseline = _build_patient_baseline(patient_id, enc_start, conn)
    death_encounter = _build_encounter_details(encounter_id, conn)

    # Prior care: encounters in lookback window before the death encounter
    prior_encounters: list[dict] = []
    recent_conditions: list[dict] = []
    active_medications: list[dict] = []
    if enc_start:
        try:
            start_date = date.fromisoformat(enc_start[:10])
            lookback_start = (start_date - timedelta(days=lookback_days)).isoformat()
        except (ValueError, TypeError):
            lookback_start = None

        if lookback_start:
            cur.execute(
                """SELECT * FROM encounters
                   WHERE patient_id = %s
                     AND id != %s
                     AND start >= %s AND start < %s
                   ORDER BY start""",
                (patient_id, encounter_id, lookback_start, enc_start),
            )
            prior_encounters = _rows_to_dicts(cur.fetchall())

            cur.execute(
                """SELECT code, display, onset, clinical_status
                   FROM conditions
                   WHERE patient_id = %s
                     AND onset >= %s AND onset < %s
                   ORDER BY onset""",
                (patient_id, lookback_start, enc_start),
            )
            recent_conditions = _rows_to_dicts(cur.fetchall())

            cur.execute(
                """SELECT code, display, start, "end", status
                   FROM medications
                   WHERE patient_id = %s
                     AND (status = 'active' OR "end" IS NULL OR "end" = '' OR "end" >= %s)
                     AND start < %s
                   ORDER BY start""",
                (patient_id, lookback_start, enc_start),
            )
            active_medications = _rows_to_dicts(cur.fetchall())

    prior_care = {
        "encounters": prior_encounters,
        "recent_conditions": recent_conditions,
        "active_medications": active_medications,
    }

    context: dict[str, Any] = {
        "case_type": "mortality",
        "case_id": case_row["id"],
        "patient_baseline": patient_baseline,
        "death_encounter": death_encounter,
        "death_date": case_row.get("death_date"),
        "lookback_days": lookback_days,
        "prior_care": prior_care,
    }
    return context


# ── PSI context ───────────────────────────────────────────────────────


def _build_encounter_details_for_psi(encounter_id: str, conn) -> dict[str, Any]:
    """Variant of _build_encounter_details that includes radiology notes.

    PSI review hinges on detecting documentation/coding gaps — uncoded
    findings in radiology reports that would change the indicator's
    eligibility. The standard ``_build_encounter_details`` only includes
    discharge summaries and admission H&Ps; this variant additionally
    pulls full text of every radiology note attached to the encounter.
    """
    details = _build_encounter_details(encounter_id, conn)

    cur = conn.cursor()
    cur.execute(
        """SELECT category, description, chartdate, charttime, text
           FROM notes
           WHERE encounter_id = %s
           ORDER BY COALESCE(charttime, chartdate)""",
        (encounter_id,),
    )
    note_rows = _rows_to_dicts(cur.fetchall())

    psi_key_notes: list[dict] = []
    for n in note_rows:
        if _is_psi_key_note(n.get("category"), n.get("description")):
            psi_key_notes.append({
                "category": n.get("category"),
                "description": n.get("description"),
                "chartdate": n.get("chartdate"),
                "charttime": n.get("charttime"),
                "text": n.get("text"),
            })

    details["key_notes"] = psi_key_notes  # Override with the wider set
    return details


def _assemble_psi_context(case_row: dict[str, Any], conn) -> dict[str, Any]:
    """Assemble a clinical context dict for an AHRQ PSI candidate case."""
    case_row = dict(case_row)
    encounter_id = case_row["encounter_id"]
    patient_id = case_row["patient_id"]
    psi_number = case_row["psi_number"]
    psi_name = case_row["psi_name"]

    cur = conn.cursor()
    cur.execute("SELECT * FROM encounters WHERE id = %s", (encounter_id,))
    enc = dict(cur.fetchone() or {})
    enc_start = enc.get("start")

    patient_baseline = _build_patient_baseline(patient_id, enc_start, conn)
    encounter_details = _build_encounter_details_for_psi(encounter_id, conn)

    # Prior admissions (last 5) for POA context
    cur.execute(
        """SELECT id, start, "end", reason_code, reason_display, discharge_disposition
           FROM encounters
           WHERE patient_id = %s
             AND id != %s
             AND start < %s
           ORDER BY start DESC
           LIMIT 5""",
        (patient_id, encounter_id, enc_start or "9999-12-31"),
    )
    prior_encounters = _rows_to_dicts(cur.fetchall())

    # Per-prior-encounter coded diagnoses (only display + code, no notes)
    for prior in prior_encounters:
        cur.execute(
            "SELECT code, display FROM conditions WHERE encounter_id = %s ORDER BY onset",
            (prior["id"],),
        )
        prior["diagnoses"] = _rows_to_dicts(cur.fetchall())

    # Decode the engine's exclusion_reasons + notes from the JSON blob
    exclusion_payload: list[str] = []
    if case_row.get("exclusion_reasons"):
        try:
            exclusion_payload = json.loads(case_row["exclusion_reasons"])
        except (json.JSONDecodeError, TypeError):
            exclusion_payload = []

    psi_metadata = {
        "psi_number": psi_number,
        "psi_name": psi_name,
        "numerator_met": case_row.get("numerator_met"),
        "denominator_met": case_row.get("denominator_met"),
        "exclusion_reasons_and_engine_notes": exclusion_payload,
        "poa_imputation_method": case_row.get("poa_imputation"),
        "poa_confidence": case_row.get("poa_confidence"),
    }

    context: dict[str, Any] = {
        "case_type": "psi",
        "case_id": case_row["id"],
        "psi": psi_metadata,
        "patient_baseline": patient_baseline,
        "encounter": encounter_details,
        "prior_encounters": prior_encounters,
    }
    return context


# ── Dispatcher ────────────────────────────────────────────────────────

_CONTEXT_BUILDERS = {
    "readmission": _assemble_readmission_context,
    "mortality": _assemble_mortality_context,
    "psi": _assemble_psi_context,
}


def assemble_context(case_type_or_row, row_or_conn=None, conn=None) -> dict[str, Any]:
    """Assemble a full clinical context dict for a case.

    Supports two call signatures for backwards compatibility:
    - assemble_context(case_type, case_row, conn) — new dispatched form
    - assemble_context(readmission_row, conn) — legacy readmission form
    """
    if isinstance(case_type_or_row, str) and case_type_or_row in _CONTEXT_BUILDERS:
        # New form: assemble_context("readmission", row, conn)
        case_type = case_type_or_row
        case_row = row_or_conn
        connection = conn
    else:
        # Legacy form: assemble_context(row, conn)
        case_type = "readmission"
        case_row = case_type_or_row
        connection = row_or_conn

    builder = _CONTEXT_BUILDERS.get(case_type)
    if not builder:
        raise ValueError(f"Unknown case type: {case_type}")

    return builder(case_row, connection)


def context_to_prompt_string(context: dict[str, Any]) -> str:
    """Serialize a context dict to a human-readable string for an LLM prompt."""
    return json.dumps(context, indent=2, default=str)
