"""Tests for src/data/ingest and src/data/pairs."""

import json
import tempfile
from pathlib import Path

import pytest

from src.schema import get_connection

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


def _enc(enc_id: str, start: str, end: str, class_code: str = "IMP", reason_code: str | None = None) -> dict:
    # Default: use the enc_id as a unique reason code so encounters are not
    # mistakenly filtered as planned readmissions (same reason_code filter).
    rc = reason_code or enc_id
    return {
        "resourceType": "Encounter",
        "id": enc_id,
        "subject": {"reference": "Patient/patient-1"},
        "class": {"code": class_code},
        "type": [{"coding": [{"code": "162673000", "display": "General examination"}]}],
        "period": {"start": start, "end": end},
        "reasonCode": [{"coding": [{"code": rc, "display": f"Reason {rc}"}]}],
        "hospitalization": {
            "dischargeDisposition": {
                "coding": [{"display": "Home"}],
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
    from src.schema import get_connection, ALL_TABLES
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

        from src.data.ingest import run_ingest
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

        from src.data.ingest import run_ingest
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
        """Insert patient + encounters directly, then run pair identification."""
        bundle = _make_bundle([PATIENT_RESOURCE] + encounters)
        bundle_dir = Path(tempfile.mkdtemp())
        (bundle_dir / "b.json").write_text(json.dumps(bundle))

        from src.data.ingest import run_ingest
        run_ingest(str(bundle_dir))

        from src.data.pairs import run_identify_pairs
        run_identify_pairs()

    def test_readmission_within_30_days(self, _clean_db, tmp_path):
        """Two inpatient encounters 10 days apart should be a pair."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        pairs = conn.execute("SELECT * FROM readmission_pairs").fetchall()
        assert len(pairs) == 1
        assert pairs[0]["index_encounter_id"] == "enc-a"
        assert pairs[0]["readmission_encounter_id"] == "enc-b"
        assert pairs[0]["days_between"] == 5  # Jan 5 -> Jan 10
        conn.close()

    def test_exactly_30_days_included(self, _clean_db, tmp_path):
        """Encounter exactly 30 days after discharge should be included."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-02-04T00:00:00Z", "2025-02-10T00:00:00Z"),  # 30 days after Jan 5
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        pairs = conn.execute("SELECT * FROM readmission_pairs").fetchall()
        assert len(pairs) == 1
        assert pairs[0]["days_between"] == 30
        conn.close()

    def test_31_days_excluded(self, _clean_db, tmp_path):
        """Encounter 31 days after discharge should NOT be paired."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-02-05T00:00:00Z", "2025-02-10T00:00:00Z"),  # 31 days after Jan 5
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        pairs = conn.execute("SELECT * FROM readmission_pairs").fetchall()
        assert len(pairs) == 0
        conn.close()

    def test_ambulatory_excluded(self, _clean_db, tmp_path):
        """Ambulatory encounters should not form readmission pairs."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z", class_code="AMB"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z", class_code="AMB"),
        ]
        self._setup_encounters(encs)

        conn = get_connection()
        pairs = conn.execute("SELECT * FROM readmission_pairs").fetchall()
        assert len(pairs) == 0
        conn.close()

    def test_idempotent_pairs(self, _clean_db, tmp_path):
        """Running identify_pairs twice should not duplicate pairs."""
        encs = [
            _enc("enc-a", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
            _enc("enc-b", "2025-01-10T00:00:00Z", "2025-01-15T00:00:00Z"),
        ]
        self._setup_encounters(encs)

        # Run again
        from src.data.pairs import run_identify_pairs
        run_identify_pairs()

        conn = get_connection()
        pairs = conn.execute("SELECT * FROM readmission_pairs").fetchall()
        assert len(pairs) == 1
        conn.close()
