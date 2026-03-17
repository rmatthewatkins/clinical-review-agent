"""Tests for the readmission review agent: context assembly and response parsing."""

import json
import sqlite3
from datetime import date

import pytest

from src.agent.context import assemble_context, context_to_prompt_string, _compute_age
from src.agent.reviewer import _parse_response
from src.schema import get_connection


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Create an in-memory SQLite database with schema and sample data."""
    conn = get_connection(":memory:")

    # Insert a patient
    conn.execute(
        "INSERT INTO patients (id, birth_date, gender, race, ethnicity, city, state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("P001", "1955-06-15", "M", "white", "nonhispanic", "Springfield", "IL"),
    )

    # Insert index encounter
    conn.execute(
        "INSERT INTO encounters (id, patient_id, type, start, end, reason_code, reason_display, discharge_disposition) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("E001", "P001", "inpatient", "2025-01-10", "2025-01-14", "I50.9", "Heart failure, unspecified", "home"),
    )

    # Insert readmission encounter
    conn.execute(
        "INSERT INTO encounters (id, patient_id, type, start, end, reason_code, reason_display, discharge_disposition) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("E003", "P001", "inpatient", "2025-01-28", "2025-02-02", "I50.9", "Heart failure, unspecified", "home"),
    )

    # Insert an interval encounter (outpatient visit between)
    conn.execute(
        "INSERT INTO encounters (id, patient_id, type, start, end, reason_code, reason_display, discharge_disposition) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("E002", "P001", "outpatient", "2025-01-20", "2025-01-20", "Z00.0", "Follow-up visit", None),
    )

    # Index encounter diagnoses
    conn.execute(
        "INSERT INTO conditions (id, patient_id, encounter_id, code, display, onset, abatement, clinical_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("C001", "P001", "E001", "I50.9", "Heart failure", "2025-01-10", None, "active"),
    )
    conn.execute(
        "INSERT INTO conditions (id, patient_id, encounter_id, code, display, onset, abatement, clinical_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("C002", "P001", "E001", "I10", "Essential hypertension", "2020-03-01", None, "active"),
    )

    # Readmission diagnoses
    conn.execute(
        "INSERT INTO conditions (id, patient_id, encounter_id, code, display, onset, abatement, clinical_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("C003", "P001", "E003", "I50.9", "Heart failure exacerbation", "2025-01-28", None, "active"),
    )

    # A new condition in the interval
    conn.execute(
        "INSERT INTO conditions (id, patient_id, encounter_id, code, display, onset, abatement, clinical_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("C004", "P001", "E002", "E11.9", "Type 2 diabetes", "2025-01-20", None, "active"),
    )

    # Index encounter medications
    conn.execute(
        "INSERT INTO medications (id, patient_id, encounter_id, code, display, start, end, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("M001", "P001", "E001", "197361", "Furosemide 40mg", "2025-01-10", None, "active"),
    )
    conn.execute(
        "INSERT INTO medications (id, patient_id, encounter_id, code, display, start, end, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("M002", "P001", "E001", "29046", "Lisinopril 10mg", "2025-01-10", None, "active"),
    )

    # A new medication started in the interval
    conn.execute(
        "INSERT INTO medications (id, patient_id, encounter_id, code, display, start, end, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("M003", "P001", "E002", "860975", "Metformin 500mg", "2025-01-20", None, "active"),
    )

    # Index encounter procedures
    conn.execute(
        "INSERT INTO procedures (id, patient_id, encounter_id, code, display, date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("PR001", "P001", "E001", "93306", "Echocardiogram", "2025-01-11"),
    )

    # Index encounter observations
    conn.execute(
        "INSERT INTO observations (id, patient_id, encounter_id, code, display, value, unit, date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("O001", "P001", "E001", "85354-9", "Blood pressure systolic", "158", "mmHg", "2025-01-10"),
    )
    conn.execute(
        "INSERT INTO observations (id, patient_id, encounter_id, code, display, value, unit, date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("O002", "P001", "E001", "39156-5", "BMI", "32.1", "kg/m2", "2025-01-10"),
    )

    # Readmission observations
    conn.execute(
        "INSERT INTO observations (id, patient_id, encounter_id, code, display, value, unit, date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("O003", "P001", "E003", "85354-9", "Blood pressure systolic", "172", "mmHg", "2025-01-28"),
    )

    # Readmission pair
    conn.execute(
        "INSERT INTO readmission_pairs (id, index_encounter_id, readmission_encounter_id, days_between, patient_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (1, "E001", "E003", 14, "P001"),
    )

    conn.commit()
    return conn


# ── Context assembly tests ──────────────────────────────────────────────


class TestContextAssembly:
    def test_context_has_required_top_level_keys(self, db):
        pair = db.execute("SELECT * FROM readmission_pairs WHERE id = 1").fetchone()
        ctx = assemble_context(pair, db)
        assert "pair_id" in ctx
        assert "days_between" in ctx
        assert "patient_baseline" in ctx
        assert "index_admission" in ctx
        assert "interval_care" in ctx
        assert "readmission" in ctx

    def test_patient_baseline(self, db):
        pair = db.execute("SELECT * FROM readmission_pairs WHERE id = 1").fetchone()
        ctx = assemble_context(pair, db)
        baseline = ctx["patient_baseline"]

        assert baseline["patient_id"] == "P001"
        assert baseline["gender"] == "M"
        assert baseline["race"] == "white"
        assert baseline["ethnicity"] == "nonhispanic"
        assert isinstance(baseline["age"], int)
        assert baseline["age"] >= 69  # Born 1955, index in 2025
        assert len(baseline["chronic_conditions"]) >= 2  # HF + HTN at minimum
        assert len(baseline["active_medications"]) >= 2  # Furosemide + Lisinopril

    def test_index_admission(self, db):
        pair = db.execute("SELECT * FROM readmission_pairs WHERE id = 1").fetchone()
        ctx = assemble_context(pair, db)
        idx = ctx["index_admission"]

        assert idx["encounter_id"] == "E001"
        assert idx["start"] == "2025-01-10"
        assert idx["end"] == "2025-01-14"
        assert idx["length_of_stay_days"] == 4
        assert idx["reason_display"] == "Heart failure, unspecified"
        assert idx["discharge_disposition"] == "home"
        assert len(idx["diagnoses"]) >= 2
        assert len(idx["procedures"]) >= 1
        assert len(idx["medications"]) >= 2
        assert len(idx["observations"]) >= 2

    def test_interval_care(self, db):
        pair = db.execute("SELECT * FROM readmission_pairs WHERE id = 1").fetchone()
        ctx = assemble_context(pair, db)
        interval = ctx["interval_care"]

        assert len(interval["encounters"]) >= 1
        assert interval["encounters"][0]["id"] == "E002"
        assert len(interval["new_medications_started"]) >= 1
        assert any("Metformin" in m["display"] for m in interval["new_medications_started"])
        assert len(interval["new_conditions_diagnosed"]) >= 1
        assert any("diabetes" in c["display"].lower() for c in interval["new_conditions_diagnosed"])

    def test_readmission(self, db):
        pair = db.execute("SELECT * FROM readmission_pairs WHERE id = 1").fetchone()
        ctx = assemble_context(pair, db)
        readmit = ctx["readmission"]

        assert readmit["encounter_id"] == "E003"
        assert readmit["start"] == "2025-01-28"
        assert readmit["reason_display"] == "Heart failure, unspecified"
        assert len(readmit["diagnoses"]) >= 1
        assert len(readmit["observations"]) >= 1

    def test_days_between(self, db):
        pair = db.execute("SELECT * FROM readmission_pairs WHERE id = 1").fetchone()
        ctx = assemble_context(pair, db)
        assert ctx["days_between"] == 14

    def test_context_serializable(self, db):
        pair = db.execute("SELECT * FROM readmission_pairs WHERE id = 1").fetchone()
        ctx = assemble_context(pair, db)
        prompt = context_to_prompt_string(ctx)
        assert isinstance(prompt, str)
        # Should be valid JSON
        parsed = json.loads(prompt)
        assert parsed["pair_id"] == 1


class TestComputeAge:
    def test_basic_age(self):
        assert _compute_age("1990-01-01", "2025-06-15") == 35

    def test_birthday_not_yet(self):
        assert _compute_age("1990-07-01", "2025-06-15") == 34

    def test_none_birth_date(self):
        assert _compute_age(None) is None

    def test_invalid_date(self):
        assert _compute_age("not-a-date") is None


# ── Response parsing tests ──────────────────────────────────────────────


class TestResponseParsing:
    SAMPLE_RESPONSE = '''Here is my assessment of this readmission case.

```json
{
  "root_cause_category": "inadequate_transition_planning",
  "preventability_score": 4,
  "preventability_rationale": "The patient was discharged with active heart failure symptoms and no clear follow-up plan. The discharge disposition to home without home health was inappropriate given the severity of the condition. A 4-day length of stay for acute decompensated heart failure with persistent hypertension is suboptimal.",
  "contributing_factors": [
    "No follow-up appointment scheduled within 7 days of discharge",
    "Discharge to home without home health services despite complex medication regimen",
    "Inadequate blood pressure control at discharge (158 mmHg systolic)",
    "New diabetes diagnosis during interval without integration into heart failure management plan"
  ],
  "recommended_interventions": [
    "Schedule cardiology follow-up within 3-5 days of discharge for all heart failure patients",
    "Implement home health referral for patients with EF <40% and complex medication regimens",
    "Establish a discharge blood pressure target of <140 mmHg systolic before discharge",
    "Implement a pharmacist-led medication reconciliation call within 48 hours post-discharge"
  ],
  "confidence_level": "high"
}
```

This 69-year-old male with known heart failure and hypertension was admitted for acute decompensated heart failure on January 10, 2025, and discharged four days later to home. The index hospitalization was notable for persistent systolic hypertension at 158 mmHg and a BMI of 32.1, both of which are independent risk factors for heart failure readmission. The discharge plan consisted of continuation of furosemide and lisinopril without documented titration and without arrangement of early cardiology follow-up.

During the 14-day interval between discharge and readmission, the patient was seen once in an outpatient setting where a new diagnosis of type 2 diabetes was made and metformin was initiated. This visit represented the only touchpoint between the health system and the patient, and there is no evidence that the new metabolic diagnosis was integrated into the overall heart failure management strategy. The readmission on January 28 for heart failure exacerbation with worsened systolic hypertension at 172 mmHg demonstrates a clear trajectory of decompensation that was predictable and likely preventable.

The core failure in this case was inadequate transition planning. The patient required closer post-discharge monitoring given the severity of his presentation, the persistent hypertension, and the complexity of his comorbid conditions. A structured heart failure discharge protocol with mandatory 48-hour telephone follow-up, 7-day clinic visit, and clear weight-monitoring instructions would likely have identified the decompensation trajectory earlier and allowed for outpatient intervention.'''

    def test_parse_extracts_json(self):
        structured, narrative = _parse_response(self.SAMPLE_RESPONSE)
        assert structured["root_cause_category"] == "inadequate_transition_planning"
        assert structured["preventability_score"] == 4
        assert isinstance(structured["contributing_factors"], list)
        assert len(structured["contributing_factors"]) == 4
        assert isinstance(structured["recommended_interventions"], list)
        assert len(structured["recommended_interventions"]) == 4
        assert structured["confidence_level"] == "high"
        assert "preventability_rationale" in structured

    def test_parse_extracts_narrative(self):
        structured, narrative = _parse_response(self.SAMPLE_RESPONSE)
        assert len(narrative) > 200
        assert "69-year-old male" in narrative
        assert "heart failure" in narrative.lower()
        # Should not contain the JSON
        assert "```json" not in narrative

    def test_parse_valid_enum_values(self):
        structured, _ = _parse_response(self.SAMPLE_RESPONSE)
        valid_categories = {
            "premature_discharge", "inadequate_transition_planning",
            "medication_related", "inadequate_follow_up",
            "disease_progression", "social_determinants",
            "patient_behavioral", "unavoidable", "other",
        }
        assert structured["root_cause_category"] in valid_categories
        assert structured["preventability_score"] in range(1, 6)
        assert structured["confidence_level"] in {"high", "moderate", "low"}

    def test_parse_raises_on_no_json(self):
        with pytest.raises(ValueError, match="Could not extract structured JSON"):
            _parse_response("This response has no JSON at all, just plain text without any braces.")
