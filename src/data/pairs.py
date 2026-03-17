"""Identify 30-day readmission pairs from inpatient encounters."""

import logging

from src.schema import get_connection

logger = logging.getLogger(__name__)


def run_identify_pairs() -> None:
    """Find all 30-day inpatient readmission pairs and store them."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    conn = get_connection()

    # Clear previous pairs and their reviews for idempotency
    conn.execute("DELETE FROM reviews")
    conn.execute("DELETE FROM readmission_pairs")

    # Fetch all inpatient encounters ordered by patient then start date
    rows = conn.execute(
        """
        SELECT id, patient_id, start, "end", reason_code, reason_display
        FROM encounters
        WHERE type = 'inpatient'
        ORDER BY patient_id, start
        """
    ).fetchall()

    from datetime import date as _date

    pairs: list[tuple] = []
    skipped_planned = 0
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

                if 0 <= days <= 30:
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

                    pairs.append((
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

    if skipped_planned:
        logger.info("Skipped %d planned readmission(s).", skipped_planned)

    if pairs:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO readmission_pairs (index_encounter_id, readmission_encounter_id, days_between, patient_id) VALUES (%s, %s, %s, %s)",
                pairs,
            )

    conn.commit()
    conn.close()

    logger.info("Identified %d readmission pairs.", len(pairs))
