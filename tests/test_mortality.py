"""Tests for mortality case identification and context assembly."""

import json
import tempfile
from pathlib import Path

import pytest

from clinical_review_agent.schema import get_connection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bundle(resources: list[dict]) -> dict:
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": r} for r in resources],
    }


PATIENT_RESOURCE = {
    "resourceType": "Patient",
    "id": "patient-1",
    "birthDate": "1960-05-15",
    "gender": "male",
    "extension": [
        {
            "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race",
            "extension": [{"url": "text", "valueString": "White"}],
        },
        {
            "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity",
            "extension": [{"url": "text", "valueString": "Not Hispanic or Latino"}],
        },
    ],
    "address": [{"city": "Boston", "state": "MA"}],
}


def _enc(
    enc_id: str,
    start: str,
    end: str,
    class_code: str = "IMP",
    discharge_disposition: str | None = "Home",
    reason_display: str | None = None,
) -> dict:
    return {
        "resourceType": "Encounter",
        "id": enc_id,
        "subject": {"reference": "Patient/patient-1"},
        "class": {"code": class_code},
        "type": [{"coding": [{"code": "162673000", "display": "General examination"}]}],
        "period": {"start": start, "end": end},
        "reasonCode": [{"coding": [{"code": enc_id, "display": reason_display or f"Reason {enc_id}"}]}],
        "hospitalization": {
            "dischargeDisposition": {
                "coding": [{"display": discharge_disposition or ""}],
            }
        },
    }


@pytest.fixture()
def _clean_db():
    """Ensure a clean database for each test."""
    from clinical_review_agent.schema import get_connection, ALL_TABLES
    conn = get_connection()
    for table in ALL_TABLES:
        conn.execute(f"TRUNCATE {table} CASCADE")
    conn.commit()
    conn.close()


def _ingest_and_identify_mortality(encounters: list[dict]):
    """Insert patient + encounters, then run mortality identification."""
    bundle = _make_bundle([PATIENT_RESOURCE] + encounters)
    bundle_dir = Path(tempfile.mkdtemp())
    (bundle_dir / "b.json").write_text(json.dumps(bundle))

    from clinical_review_agent.data.ingest import run_ingest
    run_ingest(str(bundle_dir))

    from clinical_review_agent.data.mortality import run_identify_mortality
    run_identify_mortality()


# ---------------------------------------------------------------------------
# Mortality identification tests
# ---------------------------------------------------------------------------


class TestMortalityIdentification:
    def test_finds_death_encounters(self, _clean_db):
        """Encounters with expired disposition should be identified as mortality cases."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-10T00:00:00Z",
                 discharge_disposition="Expired"),
        ]
        _ingest_and_identify_mortality(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM mortality_cases").fetchall()
        assert len(rows) == 1
        assert rows[0]["encounter_id"] == "enc-a"
        assert rows[0]["patient_id"] == "patient-1"
        assert rows[0]["death_date"] == "2025-01-10"
        conn.close()

    def test_ignores_non_death_encounters(self, _clean_db):
        """Encounters with normal disposition should NOT be mortality cases."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-10T00:00:00Z",
                 discharge_disposition="Home"),
        ]
        _ingest_and_identify_mortality(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM mortality_cases").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_multiple_death_patterns(self, _clean_db):
        """Various death disposition patterns should all be recognized."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z",
                 discharge_disposition="Patient died"),
            _enc("enc-b", "2025-02-01T00:00:00Z", "2025-02-10T00:00:00Z",
                 discharge_disposition="Deceased"),
        ]
        _ingest_and_identify_mortality(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM mortality_cases ORDER BY id").fetchall()
        assert len(rows) == 2
        conn.close()

    def test_idempotent(self, _clean_db):
        """Running identify twice should not duplicate mortality cases."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-10T00:00:00Z",
                 discharge_disposition="Expired"),
        ]
        _ingest_and_identify_mortality(encs)

        # Run again
        from clinical_review_agent.data.mortality import run_identify_mortality
        run_identify_mortality()

        conn = get_connection()
        rows = conn.execute("SELECT * FROM mortality_cases").fetchall()
        assert len(rows) == 1
        conn.close()

    def test_does_not_destroy_readmission_reviews(self, _clean_db):
        """Re-identifying mortality should not delete readmission reviews."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z"),
            _enc("enc-c", "2025-02-01T00:00:00Z", "2025-02-10T00:00:00Z",
                 discharge_disposition="Expired"),
        ]
        bundle = _make_bundle([PATIENT_RESOURCE] + encs)
        bundle_dir = Path(tempfile.mkdtemp())
        (bundle_dir / "b.json").write_text(json.dumps(bundle))

        from clinical_review_agent.data.ingest import run_ingest
        run_ingest(str(bundle_dir))

        # Identify readmissions first
        from clinical_review_agent.data.pairs import run_identify_readmissions
        run_identify_readmissions()

        conn = get_connection()
        # Insert a fake readmission review
        ra = conn.execute("SELECT id FROM readmissions LIMIT 1").fetchone()
        if ra:
            conn.execute(
                "INSERT INTO reviews (case_type, case_id, structured_json, clinical_narrative, model_used, created_at, tokens_used) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ("readmission", ra["id"], '{"root_cause_category": "test"}', "Test.", "test-model", "2025-01-01", 100),
            )
            conn.commit()

        # Now identify mortality — should NOT touch readmission reviews
        from clinical_review_agent.data.mortality import run_identify_mortality
        run_identify_mortality()

        readmission_reviews = conn.execute(
            "SELECT COUNT(*) AS cnt FROM reviews WHERE case_type = 'readmission'"
        ).fetchone()["cnt"]
        assert readmission_reviews >= 1 if ra else True
        conn.close()


# ---------------------------------------------------------------------------
# Mortality context assembly tests
# ---------------------------------------------------------------------------


class TestMortalityContext:
    def test_context_has_required_keys(self, db_conn):
        """Mortality context should have expected top-level keys."""
        conn = db_conn

        # Insert test data
        conn.execute(
            "INSERT INTO patients (id, birth_date, gender, race, city, state) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("P001", "1950-01-01", "M", "white", "Boston", "MA"),
        )
        conn.execute(
            """INSERT INTO encounters (id, patient_id, type, start, "end", reason_display, discharge_disposition) """
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("E001", "P001", "inpatient", "2025-01-01", "2025-01-10", "Pneumonia", "Expired"),
        )
        conn.execute(
            "INSERT INTO mortality_cases (id, encounter_id, patient_id, death_date, lookback_days) "
            "VALUES (%s, %s, %s, %s, %s)",
            (1, "E001", "P001", "2025-01-10", 90),
        )
        conn.commit()

        from clinical_review_agent.agent.context import assemble_context
        row = conn.execute("SELECT * FROM mortality_cases WHERE id = 1").fetchone()
        ctx = assemble_context("mortality", row, conn)

        assert ctx["case_type"] == "mortality"
        assert ctx["case_id"] == 1
        assert "patient_baseline" in ctx
        assert "death_encounter" in ctx
        assert "prior_care" in ctx
        assert ctx["death_date"] == "2025-01-10"

    def test_death_encounter_details(self, db_conn):
        """Death encounter should include diagnoses, procedures, observations."""
        conn = db_conn

        conn.execute(
            "INSERT INTO patients (id, birth_date, gender) VALUES (%s, %s, %s)",
            ("P001", "1950-01-01", "M"),
        )
        conn.execute(
            """INSERT INTO encounters (id, patient_id, type, start, "end", reason_display, discharge_disposition) """
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("E001", "P001", "inpatient", "2025-01-01", "2025-01-10", "Pneumonia", "Expired"),
        )
        conn.execute(
            "INSERT INTO conditions (id, patient_id, encounter_id, code, display, onset, clinical_status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("C001", "P001", "E001", "J18.9", "Pneumonia", "2025-01-01", "active"),
        )
        conn.execute(
            "INSERT INTO mortality_cases (id, encounter_id, patient_id, death_date, lookback_days) "
            "VALUES (%s, %s, %s, %s, %s)",
            (1, "E001", "P001", "2025-01-10", 90),
        )
        conn.commit()

        from clinical_review_agent.agent.context import assemble_context
        row = conn.execute("SELECT * FROM mortality_cases WHERE id = 1").fetchone()
        ctx = assemble_context("mortality", row, conn)

        de = ctx["death_encounter"]
        assert de["encounter_id"] == "E001"
        assert de["discharge_disposition"] == "Expired"
        assert len(de["diagnoses"]) >= 1

    def test_prior_care_lookback(self, db_conn):
        """Prior care should include encounters within the lookback window."""
        conn = db_conn

        conn.execute(
            "INSERT INTO patients (id, birth_date, gender) VALUES (%s, %s, %s)",
            ("P001", "1950-01-01", "M"),
        )
        # Prior encounter (within 90-day lookback)
        conn.execute(
            """INSERT INTO encounters (id, patient_id, type, start, "end", reason_display) """
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("E000", "P001", "outpatient", "2024-12-01", "2024-12-01", "Checkup"),
        )
        # Death encounter
        conn.execute(
            """INSERT INTO encounters (id, patient_id, type, start, "end", reason_display, discharge_disposition) """
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("E001", "P001", "inpatient", "2025-01-01", "2025-01-10", "Pneumonia", "Expired"),
        )
        conn.execute(
            "INSERT INTO mortality_cases (id, encounter_id, patient_id, death_date, lookback_days) "
            "VALUES (%s, %s, %s, %s, %s)",
            (1, "E001", "P001", "2025-01-10", 90),
        )
        conn.commit()

        from clinical_review_agent.agent.context import assemble_context
        row = conn.execute("SELECT * FROM mortality_cases WHERE id = 1").fetchone()
        ctx = assemble_context("mortality", row, conn)

        assert len(ctx["prior_care"]["encounters"]) == 1
        assert ctx["prior_care"]["encounters"][0]["id"] == "E000"
