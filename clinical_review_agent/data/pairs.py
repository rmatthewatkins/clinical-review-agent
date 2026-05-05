"""Identify 30-day readmissions from inpatient encounters."""

import logging

from clinical_review_agent.config import get_settings
from clinical_review_agent.schema import get_connection

logger = logging.getLogger(__name__)

_TRANSFER_DISPOSITION_PATTERNS = (
    "skilled nursing",
    "transferred to",
    "rehabilitation",
    "hospice",
    "long term care",
    "snf",
)

_PLANNED_FOLLOWUP_PATTERNS = (
    "history of",
    "patient transfer",
    "aftercare",
    "post-operative",
    "follow-up",
    "followup",
)


def _is_transfer_disposition(disposition: str | None) -> bool:
    """Return True if the discharge disposition indicates a facility transfer."""
    if not disposition:
        return False
    lower = disposition.lower()
    return any(pat in lower for pat in _TRANSFER_DISPOSITION_PATTERNS)


def _is_planned_followup(reason_display: str | None) -> bool:
    """Return True if the readmission reason suggests a planned follow-up."""
    if not reason_display:
        return False
    lower = reason_display.lower()
    return any(pat in lower for pat in _PLANNED_FOLLOWUP_PATTERNS)


def run_identify_readmissions() -> None:
    """Find all 30-day inpatient readmissions and store them."""
    cfg = get_settings()
    conn = get_connection()

    # Clear previous readmissions and their reviews for idempotency
    conn.execute("DELETE FROM reviews WHERE case_type = 'readmission'")
    conn.execute("DELETE FROM readmissions")

    # Fetch all inpatient encounters ordered by patient then start date
    rows = conn.execute(
        """
        SELECT id, patient_id, start, "end", reason_code, reason_display,
               discharge_disposition
        FROM encounters
        WHERE type = 'inpatient'
        ORDER BY patient_id, start
        """
    ).fetchall()

    from datetime import date as _date

    readmissions: list[tuple] = []
    skipped_planned = 0
    skipped_transfer_gap = 0
    skipped_transfer_disposition = 0
    skipped_surgical_followup = 0
    i = 0
    while i < len(rows):
        j = i + 1
        # Walk forward while same patient
        while j < len(rows) and rows[j]["patient_id"] == rows[i]["patient_id"]:
            index_end = rows[i]["end"][:10] if rows[i]["end"] else None
            readmit_start = rows[j]["start"][:10] if rows[j]["start"] else None

            if index_end and readmit_start:
                d_end = _date.fromisoformat(index_end)
                d_start = _date.fromisoformat(readmit_start)
                days = (d_start - d_end).days

                if 0 <= days <= cfg.readmission_window_days:
                    # Filter 1: Minimum days gap (transfer exclusion)
                    # 0-1 day gaps are transfers/continuation-of-care
                    if days < cfg.readmission_min_days:
                        skipped_transfer_gap += 1
                        logger.info(
                            "Skipping transfer (gap %d days): %s -> %s",
                            days, rows[i]["id"], rows[j]["id"],
                        )
                        j += 1
                        continue

                    # Filter 2: Discharge disposition indicates transfer
                    if cfg.readmission_exclude_transfer_dispositions and _is_transfer_disposition(rows[i]["discharge_disposition"]):
                        skipped_transfer_disposition += 1
                        logger.info(
                            "Skipping transfer disposition (%s): %s -> %s",
                            rows[i]["discharge_disposition"], rows[i]["id"], rows[j]["id"],
                        )
                        j += 1
                        continue

                    # Filter 3: Planned surgical follow-up
                    if cfg.readmission_exclude_surgical_followup and _is_planned_followup(rows[j]["reason_display"]):
                        skipped_surgical_followup += 1
                        logger.info(
                            "Skipping planned follow-up (%s): %s -> %s",
                            rows[j]["reason_display"], rows[i]["id"], rows[j]["id"],
                        )
                        j += 1
                        continue

                    # Exclude planned readmissions: same reason code on both
                    # encounters indicates a scheduled treatment cycle (e.g.
                    # recurring chemotherapy), not an unplanned readmission.
                    index_reason = rows[i]["reason_code"]
                    readmit_reason = rows[j]["reason_code"]
                    if index_reason and readmit_reason and index_reason == readmit_reason:
                        skipped_planned += 1
                        logger.info(
                            "Skipping planned readmission: %s -> %s (same reason: %s)",
                            rows[i]["id"], rows[j]["id"], rows[i]["reason_display"],
                        )
                        i = j
                        break

                    readmissions.append((
                        rows[i]["id"],
                        rows[j]["id"],
                        days,
                        rows[i]["patient_id"],
                    ))
                    # This readmission becomes the next index encounter;
                    # skip to j so we don't double-count
                    i = j
                    break
            j += 1
        i += 1

    skip_total = skipped_planned + skipped_transfer_gap + skipped_transfer_disposition + skipped_surgical_followup
    if skip_total:
        logger.info(
            "Skipped %d candidate(s): %d transfer-gap, %d transfer-disposition, "
            "%d surgical-followup, %d planned-same-reason.",
            skip_total, skipped_transfer_gap, skipped_transfer_disposition,
            skipped_surgical_followup, skipped_planned,
        )

    if readmissions:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO readmissions (index_encounter_id, readmission_encounter_id, days_between, patient_id) VALUES (%s, %s, %s, %s)",
                readmissions,
            )

    conn.commit()
    conn.close()

    logger.info("Identified %d readmission(s).", len(readmissions))
