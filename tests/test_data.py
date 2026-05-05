"""Tests for src/data/ingest and src/data/pairs (readmission identification)."""

import json
import tempfile
from pathlib import Path

import pytest

from clinical_review_agent.schema import get_connection

# ---------------------------------------------------------------------------
# Fixtures
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
            "extension": [
                {"url": "text", "valueString": "White"},
            ],
        },
        {
            "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity",
            "extension": [
                {"url": "text", "valueString": "Not Hispanic or Latino"},
            ],
        },
    ],
    "address": [
        {
            "city": "Boston",
            "state": "MA",
            "extension": [
                {
                    "url": "http://hl7.org/fhir/StructureDefinition/geolocation",
                    "extension": [
                        {"url": "latitude", "valueDecimal": 42.36},
                        {"url": "longitude", "valueDecimal": -71.06},
                    ],
                }
            ],
        }
    ],
}


def _enc(
    enc_id: str,
    start: str,
    end: str,
    class_code: str = "IMP",
    reason_code: str | None = None,
    reason_display: str | None = None,
    discharge_disposition: str | None = "Home",
) -> dict:
    # Default: use the enc_id as a unique reason code so encounters are not
    # mistakenly filtered as planned readmissions (same reason_code filter).
    rc = reason_code or enc_id
    rd = reason_display or f"Reason {rc}"
    return {
        "resourceType": "Encounter",
        "id": enc_id,
        "subject": {"reference": "Patient/patient-1"},
        "class": {"code": class_code},
        "type": [{"coding": [{"code": "162673000", "display": "General examination"}]}],
        "period": {"start": start, "end": end},
        "reasonCode": [{"coding": [{"code": rc, "display": rd}]}],
        "hospitalization": {
            "dischargeDisposition": {
                "coding": [{"display": discharge_disposition or ""}],
            }
        },
    }


CONDITION_RESOURCE = {
    "resourceType": "Condition",
    "id": "cond-1",
    "subject": {"reference": "Patient/patient-1"},
    "encounter": {"reference": "Encounter/enc-1"},
    "code": {"coding": [{"code": "38341003", "display": "Hypertension"}]},
    "clinicalStatus": {"coding": [{"code": "active"}]},
    "onsetDateTime": "2025-01-01T00:00:00Z",
    "abatementDateTime": "2025-02-01T00:00:00Z",
}

MEDICATION_RESOURCE = {
    "resourceType": "MedicationRequest",
    "id": "med-1",
    "subject": {"reference": "Patient/patient-1"},
    "encounter": {"reference": "Encounter/enc-1"},
    "medicationCodeableConcept": {"coding": [{"code": "ACE", "display": "Lisinopril"}]},
    "authoredOn": "2025-01-02",
    "status": "active",
}

OBSERVATION_RESOURCE = {
    "resourceType": "Observation",
    "id": "obs-1",
    "subject": {"reference": "Patient/patient-1"},
    "encounter": {"reference": "Encounter/enc-1"},
    "code": {"coding": [{"code": "85354-9", "display": "Blood pressure"}]},
    "valueQuantity": {"value": 120, "unit": "mmHg"},
    "effectiveDateTime": "2025-01-02T10:00:00Z",
}

PROCEDURE_RESOURCE = {
    "resourceType": "Procedure",
    "id": "proc-1",
    "subject": {"reference": "Patient/patient-1"},
    "encounter": {"reference": "Encounter/enc-1"},
    "code": {"coding": [{"code": "76164006", "display": "Biopsy"}]},
    "performedDateTime": "2025-01-03T09:00:00Z",
}

CAREPLAN_RESOURCE = {
    "resourceType": "CarePlan",
    "id": "cp-1",
    "subject": {"reference": "Patient/patient-1"},
    "encounter": {"reference": "Encounter/enc-1"},
    "category": [{"coding": [{"code": "698358001", "display": "Angina self management plan"}]}],
    "period": {"start": "2025-01-01", "end": "2025-06-01"},
    "status": "active",
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


# ---------------------------------------------------------------------------
# Ingest tests
# ---------------------------------------------------------------------------

class TestIngest:
    def test_full_ingest(self, _clean_db, tmp_path):
        """Ingest a bundle with every resource type and verify all tables populated."""
        bundle = _make_bundle([
            PATIENT_RESOURCE,
            _enc("enc-1", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            CONDITION_RESOURCE,
            MEDICATION_RESOURCE,
            OBSERVATION_RESOURCE,
            PROCEDURE_RESOURCE,
            CAREPLAN_RESOURCE,
        ])

        bundle_dir = tmp_path / "fhir"
        bundle_dir.mkdir()
        (bundle_dir / "patient1.json").write_text(json.dumps(bundle))

        from clinical_review_agent.data.ingest import run_ingest
        run_ingest(str(bundle_dir))

        conn = get_connection()

        # Patient
        row = conn.execute("SELECT * FROM patients WHERE id='patient-1'").fetchone()
        assert row is not None
        assert row["gender"] == "male"
        assert row["race"] == "White"
        assert row["city"] == "Boston"
        assert row["lat"] == pytest.approx(42.36)

        # Encounter
        row = conn.execute("SELECT * FROM encounters WHERE id='enc-1'").fetchone()
        assert row is not None
        assert row["type"] == "inpatient"
        assert row["discharge_disposition"] == "Home"

        # Condition
        assert conn.execute("SELECT COUNT(*) AS cnt FROM conditions").fetchone()["cnt"] == 1

        # Medication
        assert conn.execute("SELECT COUNT(*) AS cnt FROM medications").fetchone()["cnt"] == 1

        # Observation
        row = conn.execute("SELECT * FROM observations WHERE id='obs-1'").fetchone()
        assert row["value"] == "120"
        assert row["unit"] == "mmHg"

        # Procedure
        assert conn.execute("SELECT COUNT(*) AS cnt FROM procedures").fetchone()["cnt"] == 1

        # CarePlan
        assert conn.execute("SELECT COUNT(*) AS cnt FROM care_plans").fetchone()["cnt"] == 1

        conn.close()

    def test_idempotent(self, _clean_db, tmp_path):
        """Running ingest twice should not duplicate rows."""
        bundle = _make_bundle([PATIENT_RESOURCE])
        bundle_dir = tmp_path / "fhir"
        bundle_dir.mkdir()
        (bundle_dir / "p1.json").write_text(json.dumps(bundle))

        from clinical_review_agent.data.ingest import run_ingest
        run_ingest(str(bundle_dir))
        run_ingest(str(bundle_dir))

        conn = get_connection()
        assert conn.execute("SELECT COUNT(*) AS cnt FROM patients").fetchone()["cnt"] == 1
        conn.close()


# ---------------------------------------------------------------------------
# Pair identification tests
# ---------------------------------------------------------------------------

class TestPairs:
    def _setup_encounters(self, encounters: list[dict]):
        """Insert patient + encounters directly, then run readmission identification."""
        bundle = _make_bundle([PATIENT_RESOURCE] + encounters)
        bundle_dir = Path(tempfile.mkdtemp())
        (bundle_dir / "b.json").write_text(json.dumps(bundle))

        from clinical_review_agent.data.ingest import run_ingest
        run_ingest(str(bundle_dir))

        from clinical_review_agent.data.pairs import run_identify_readmissions
        run_identify_readmissions()

    def test_readmission_within_30_days(self, _clean_db, tmp_path):
        """Two inpatient encounters 10 days apart should be a readmission."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 1
        assert rows[0]["index_encounter_id"] == "enc-a"
        assert rows[0]["readmission_encounter_id"] == "enc-b"
        assert rows[0]["days_between"] == 5  # Jan 5 -> Jan 10
        conn.close()

    def test_exactly_30_days_included(self, _clean_db, tmp_path):
        """Encounter exactly 30 days after discharge should be included."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-02-04T00:00:00Z", "2025-02-10T00:00:00Z"),  # 30 days after Jan 5
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 1
        assert rows[0]["days_between"] == 30
        conn.close()

    def test_31_days_excluded(self, _clean_db, tmp_path):
        """Encounter 31 days after discharge should NOT be a readmission."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-02-05T00:00:00Z", "2025-02-10T00:00:00Z"),  # 31 days after Jan 5
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_ambulatory_excluded(self, _clean_db, tmp_path):
        """Ambulatory encounters should not form readmissions."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z", class_code="AMB"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z", class_code="AMB"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_idempotent(self, _clean_db, tmp_path):
        """Running identify twice should not duplicate readmissions."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z"),
        ]
        self._setup_encounters(encs)

        # Run again
        from clinical_review_agent.data.pairs import run_identify_readmissions
        run_identify_readmissions()

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 1
        conn.close()

    # ── Transfer gap filter ───────────────────────────────────────────

    def test_zero_day_gap_excluded(self, _clean_db, tmp_path):
        """Same-day encounter (0-day gap) is a transfer, not a readmission."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-05T00:00:00Z", "2025-01-10T00:00:00Z"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_one_day_gap_excluded(self, _clean_db, tmp_path):
        """1-day gap is still a transfer, not a readmission."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-06T00:00:00Z", "2025-01-10T00:00:00Z"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_two_day_gap_included(self, _clean_db, tmp_path):
        """2-day gap is the boundary — should be included."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-07T00:00:00Z", "2025-01-12T00:00:00Z"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 1
        assert rows[0]["days_between"] == 2
        conn.close()

    def test_min_days_zero_restores_old_behavior(self, _clean_db, tmp_path, monkeypatch):
        """Setting READMISSION_MIN_DAYS=0 should include same-day encounters."""
        import clinical_review_agent.config
        from dataclasses import replace
        cfg = src.config.get_settings()
        monkeypatch.setattr(src.config, "settings", replace(cfg, readmission_min_days=0))

        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-05T00:00:00Z", "2025-01-10T00:00:00Z"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 1
        assert rows[0]["days_between"] == 0
        conn.close()

    # ── Discharge disposition filter ──────────────────────────────────

    def test_transfer_disposition_excluded(self, _clean_db, tmp_path):
        """Index encounter discharged to SNF should not be a readmission."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z",
                 discharge_disposition="Discharged/transferred to SNF"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_home_disposition_passes(self, _clean_db, tmp_path):
        """Home discharge disposition should not be filtered."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z",
                 discharge_disposition="Home"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 1
        conn.close()

    # ── Planned surgical follow-up filter ─────────────────────────────

    def test_history_of_reason_excluded(self, _clean_db, tmp_path):
        """Readmission with 'History of ...' reason is a planned follow-up."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z",
                 reason_display="History of CABG surgery"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_patient_transfer_reason_excluded(self, _clean_db, tmp_path):
        """Readmission with 'Patient transfer to ...' reason is a planned follow-up."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z",
                 reason_display="Patient transfer to skilled nursing facility"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_surgical_followup_disabled_restores_old_behavior(self, _clean_db, tmp_path, monkeypatch):
        """Disabling surgical follow-up filter should include 'History of' reasons."""
        import clinical_review_agent.config
        from dataclasses import replace
        cfg = src.config.get_settings()
        monkeypatch.setattr(src.config, "settings", replace(cfg, readmission_exclude_surgical_followup=False))

        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z",
                 reason_display="History of CABG surgery"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 1
        conn.close()

    # ── Multi-encounter chain tests ───────────────────────────────────

    def test_three_encounter_chain_skips_transfer(self, _clean_db, tmp_path):
        """A→B (0d transfer) → C (15d) should create readmission A→C, not A→B."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-05T00:00:00Z", "2025-01-08T00:00:00Z"),  # 0d gap = transfer
            _enc("enc-c", "2025-01-20T00:00:00Z", "2025-01-25T00:00:00Z"),  # 15d after enc-a end
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 1
        assert rows[0]["index_encounter_id"] == "enc-a"
        assert rows[0]["readmission_encounter_id"] == "enc-c"
        conn.close()

    def test_diagnostic_surgery_snf_chain_zero_readmissions(self, _clean_db, tmp_path):
        """Diagnostic → surgery → SNF chain should produce 0 readmissions."""
        encs = [
            _enc("enc-diag", "2025-01-01T00:00:00Z", "2025-01-03T00:00:00Z",
                 discharge_disposition="Discharged/transferred to Skilled Nursing Facility"),
            _enc("enc-surg", "2025-01-03T00:00:00Z", "2025-01-10T00:00:00Z",  # 0d = transfer
                 discharge_disposition="Discharged/transferred to SNF"),
            _enc("enc-snf", "2025-01-10T00:00:00Z", "2025-01-20T00:00:00Z",  # 0d = transfer
                 reason_display="Post-operative care"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        rows = conn.execute("SELECT * FROM readmissions").fetchall()
        assert len(rows) == 0
        conn.close()
