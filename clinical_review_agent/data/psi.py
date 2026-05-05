"""Identify AHRQ Patient Safety Indicator (PSI) candidate cases.

Iterates every inpatient encounter, evaluates each registered PSI against
it, and writes one ``psi_cases`` row per PSI whose numerator fired
(regardless of whether the denominator was met — surfacing exclusion
reasons is the whole point of the review-agent stage, since coding gaps
can flip a denominator-excluded case into an actual hit).

POA strategy
------------
MIMIC-IV does not carry present-on-admission flags. Each PSI infers POA
via a **prior-admission lookback**: if the same ICD code (or family) was
recorded on any earlier admission for the patient, the condition is
likely POA and we flag it. This biases toward over-counting hospital-
acquired events (since absence of prior history doesn't prove the
condition is new), which is the side the review agent is positioned to
correct by reading discharge and radiology notes.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from clinical_review_agent.data.psi_definitions import REGISTRY, PsiEvaluation
from clinical_review_agent.schema import get_connection

logger = logging.getLogger(__name__)


def _compute_age(birth_date: str | None, reference: str | None) -> int | None:
    if not birth_date or not reference:
        return None
    try:
        bd = date.fromisoformat(birth_date[:10])
        ref = date.fromisoformat(reference[:10])
    except (ValueError, TypeError):
        return None
    return ref.year - bd.year - ((ref.month, ref.day) < (bd.month, bd.day))


def run_identify_psis() -> None:
    """Run every registered PSI against every inpatient encounter."""
    conn = get_connection()

    # Idempotent: clear previous PSI cases and their reviews
    conn.execute("DELETE FROM reviews WHERE case_type = 'psi'")
    conn.execute("DELETE FROM psi_cases")
    conn.commit()

    # ── Bulk pre-fetch: 3 queries instead of 3N round-trips ─────────────
    # Long-running per-encounter loops over a remote Postgres (Railway)
    # were timing out with `server closed the connection unexpectedly`.
    # Pull everything into memory once, then iterate.
    encounters = [
        dict(r) for r in conn.execute(
            """SELECT e.id, e.patient_id, e.type, e.start, e."end",
                      e.reason_code, e.reason_display, e.discharge_disposition,
                      p.birth_date
               FROM encounters e
               JOIN patients p ON p.id = e.patient_id
               WHERE e.type = 'inpatient'
               ORDER BY e.patient_id, e.start"""
        ).fetchall()
    ]

    if not encounters:
        logger.warning("No inpatient encounters found — nothing to evaluate.")
        conn.close()
        return

    # All conditions joined with their encounter's start date — used both
    # for the per-encounter dx list and for the prior-admission lookup.
    cond_rows = conn.execute(
        """SELECT c.encounter_id, c.patient_id, c.code, c.display,
                  c.onset, c.clinical_status, e.start AS enc_start
           FROM conditions c
           JOIN encounters e ON e.id = c.encounter_id"""
    ).fetchall()

    proc_rows = conn.execute(
        """SELECT encounter_id, code, display, date FROM procedures"""
    ).fetchall()

    # Index conditions by encounter, and build (patient_id → list of (start, code))
    diagnoses_by_enc: dict[str, list[dict]] = {}
    patient_history: dict[str, list[tuple[str, str]]] = {}
    for r in cond_rows:
        enc_id = r["encounter_id"]
        diagnoses_by_enc.setdefault(enc_id, []).append({
            "code": r["code"],
            "display": r["display"],
            "onset": r["onset"],
            "clinical_status": r["clinical_status"],
        })
        if r["code"]:
            patient_history.setdefault(r["patient_id"], []).append(
                (r["enc_start"] or "", r["code"])
            )

    procedures_by_enc: dict[str, list[dict]] = {}
    for r in proc_rows:
        procedures_by_enc.setdefault(r["encounter_id"], []).append({
            "code": r["code"],
            "display": r["display"],
            "date": r["date"],
        })

    logger.info(
        "Evaluating %d PSI(s) over %d inpatient encounters.",
        len(REGISTRY), len(encounters),
    )

    rows_to_insert: list[tuple] = []
    summary: dict[str, dict[str, int]] = {
        psi.number: {"numerator": 0, "denominator": 0, "excluded": 0}
        for psi in REGISTRY
    }
    now = datetime.now(timezone.utc).isoformat()

    for encounter in encounters:
        encounter_id = encounter["id"]
        patient_id = encounter["patient_id"]
        admission_start = encounter.get("start") or ""

        diagnoses = diagnoses_by_enc.get(encounter_id, [])
        procedures = procedures_by_enc.get(encounter_id, [])

        # Prior-admission diagnosis codes — anything coded on a strictly
        # earlier encounter for this patient.
        prior_codes = {
            code for (enc_start, code) in patient_history.get(patient_id, [])
            if enc_start and enc_start < admission_start
        }

        age = _compute_age(encounter.get("birth_date"), admission_start)

        ctx = {
            "diagnoses": diagnoses,
            "procedures": procedures,
            "prior_diagnosis_codes": prior_codes,
            "age": age,
        }

        for psi in REGISTRY:
            try:
                result: PsiEvaluation = psi.evaluate(encounter, ctx)
            except Exception:  # pragma: no cover
                logger.exception(
                    "PSI %s evaluation failed on encounter %s", psi.number, encounter_id,
                )
                continue

            if not result.numerator_met:
                continue  # Skip cases with no numerator hit — not interesting

            summary[psi.number]["numerator"] += 1
            if result.denominator_met:
                summary[psi.number]["denominator"] += 1
            if result.exclusion_reasons:
                summary[psi.number]["excluded"] += 1

            rows_to_insert.append((
                result.encounter_id,
                result.patient_id,
                psi.number,
                psi.name,
                result.numerator_met,
                result.denominator_met,
                json.dumps(result.exclusion_reasons + result.notes) if (result.exclusion_reasons or result.notes) else None,
                result.poa_imputation,
                result.poa_confidence,
                now,
            ))

    if rows_to_insert:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO psi_cases
                   (encounter_id, patient_id, psi_number, psi_name,
                    numerator_met, denominator_met, exclusion_reasons,
                    poa_imputation, poa_confidence, identified_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                rows_to_insert,
            )

    conn.commit()
    conn.close()

    total_num = sum(s["numerator"] for s in summary.values())
    total_denom = sum(s["denominator"] for s in summary.values())
    total_excl = sum(s["excluded"] for s in summary.values())
    logger.info(
        "PSI identification complete: %d numerator hits, %d denominator-eligible, "
        "%d excluded with reasons.",
        total_num, total_denom, total_excl,
    )
    for psi_num, counts in summary.items():
        logger.info(
            "  PSI %s: %d numerator, %d denominator, %d excluded",
            psi_num, counts["numerator"], counts["denominator"], counts["excluded"],
        )
