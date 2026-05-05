"""Identify inpatient mortality cases for peer review."""

import logging

from clinical_review_agent.config import get_settings
from clinical_review_agent.schema import get_connection

logger = logging.getLogger(__name__)

_DEATH_DISPOSITION_PATTERNS = (
    "expired",
    "died",
    "death",
    "deceased",
)


def _is_death_disposition(disposition: str | None) -> bool:
    """Return True if discharge disposition indicates patient death."""
    if not disposition:
        return False
    lower = disposition.lower()
    return any(pat in lower for pat in _DEATH_DISPOSITION_PATTERNS)


def run_identify_mortality() -> None:
    """Find inpatient encounters where the patient died and store them."""
    cfg = get_settings()
    conn = get_connection()

    # Clear previous mortality cases and their reviews for idempotency
    conn.execute("DELETE FROM reviews WHERE case_type = 'mortality'")
    conn.execute("DELETE FROM mortality_cases")

    # Fetch all inpatient encounters with death-related discharge dispositions
    rows = conn.execute(
        """
        SELECT id, patient_id, "end", discharge_disposition
        FROM encounters
        WHERE type = 'inpatient'
          AND discharge_disposition IS NOT NULL
        ORDER BY patient_id, start
        """
    ).fetchall()

    cases: list[tuple] = []
    for row in rows:
        if _is_death_disposition(row["discharge_disposition"]):
            death_date = row["end"][:10] if row["end"] else None
            cases.append((
                row["id"],
                row["patient_id"],
                death_date,
                cfg.mortality_lookback_days,
            ))

    if cases:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO mortality_cases (encounter_id, patient_id, death_date, lookback_days) "
                "VALUES (%s, %s, %s, %s)",
                cases,
            )

    conn.commit()
    conn.close()

    logger.info("Identified %d mortality case(s).", len(cases))
