"""Tests for FHIR source adapters and parser compatibility with Epic-style resources."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from src.data.sources.base import FhirSource, code_display, extension_value, ref
from src.data.sources.synthea_file import SyntheaFileSource
from src.data.ingest import (
    _parse_encounter,
    _parse_medication,
    _parse_observation,
    _parse_patient,
)


# ── ref() ────────────────────────────────────────────────────────────────────


class TestRef:
    def test_simple_resource_ref(self):
        assert ref("Patient/abc") == "abc"

    def test_urn_uuid(self):
        assert ref("urn:uuid:abc") == "abc"

    def test_full_url(self):
        assert ref("https://server/fhir/Patient/abc") == "abc"

    def test_none(self):
        assert ref(None) is None

    def test_bare_id(self):
        assert ref("abc") == "abc"

    def test_empty_string(self):
        assert ref("") is None


# ── code_display() ───────────────────────────────────────────────────────────


class TestCodeDisplay:
    def test_full_codeable_concept(self):
        cc = {
            "coding": [{"code": "123", "display": "Hypertension"}],
            "text": "High blood pressure",
        }
        assert code_display(cc) == ("123", "Hypertension")

    def test_text_only(self):
        cc = {"text": "High blood pressure"}
        assert code_display(cc) == (None, "High blood pressure")

    def test_none(self):
        assert code_display(None) == (None, None)

    def test_empty_coding(self):
        cc = {"coding": []}
        assert code_display(cc) == (None, None)


# ── extension_value() ───────────────────────────────────────────────────────


class TestExtensionValue:
    def test_us_core_race(self):
        exts = [
            {
                "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race",
                "extension": [
                    {"url": "ombCategory", "valueCoding": {"code": "2106-3", "display": "White"}},
                    {"url": "text", "valueString": "White"},
                ],
            }
        ]
        assert extension_value(exts, "us-core-race") == "White"

    def test_direct_value_string(self):
        exts = [{"url": "http://example.org/birthsex", "valueString": "M"}]
        assert extension_value(exts, "birthsex") == "M"

    def test_value_coding_fallback(self):
        exts = [
            {
                "url": "http://example.org/language",
                "valueCoding": {"code": "en", "display": "English"},
            }
        ]
        assert extension_value(exts, "language") == "English"

    def test_missing_extension(self):
        exts = [{"url": "http://example.org/other", "valueString": "X"}]
        assert extension_value(exts, "birthsex") is None

    def test_none_extensions(self):
        assert extension_value(None, "anything") is None

    def test_empty_list(self):
        assert extension_value([], "anything") is None


# ── SyntheaFileSource ────────────────────────────────────────────────────────


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    """Create a temp directory with a minimal FHIR Bundle JSON."""
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "p1",
                    "gender": "female",
                }
            },
            {
                "resource": {
                    "resourceType": "Encounter",
                    "id": "e1",
                    "subject": {"reference": "Patient/p1"},
                }
            },
        ],
    }
    (tmp_path / "bundle.json").write_text(json.dumps(bundle))
    return tmp_path


class TestSyntheaFileSource:
    def test_iter_resources(self, bundle_dir: Path):
        source = SyntheaFileSource(bundle_dir)
        resources = list(source.iter_resources())
        assert len(resources) == 2
        assert resources[0]["resourceType"] == "Patient"
        assert resources[0]["id"] == "p1"
        assert resources[1]["resourceType"] == "Encounter"

    def test_empty_directory(self, tmp_path: Path):
        source = SyntheaFileSource(tmp_path)
        assert list(source.iter_resources()) == []

    def test_file_with_no_entries(self, tmp_path: Path):
        bundle = {"resourceType": "Bundle", "type": "collection"}
        (tmp_path / "empty.json").write_text(json.dumps(bundle))
        source = SyntheaFileSource(tmp_path)
        assert list(source.iter_resources()) == []

    def test_protocol_conformance(self, bundle_dir: Path):
        source = SyntheaFileSource(bundle_dir)
        assert isinstance(source, FhirSource)


# ── Epic-style FHIR resource parsing ────────────────────────────────────────


class TestEpicPatient:
    """Patient with address and extension parsing (Epic-like full URLs are
    irrelevant for Patient itself, but we verify address/extension extraction)."""

    def test_address_and_extension(self):
        resource = {
            "resourceType": "Patient",
            "id": "epic-p1",
            "birthDate": "1980-05-12",
            "gender": "male",
            "address": [
                {
                    "city": "Madison",
                    "state": "WI",
                    "extension": [
                        {
                            "url": "http://hl7.org/fhir/StructureDefinition/geolocation",
                            "extension": [
                                {"url": "latitude", "valueDecimal": 43.07},
                                {"url": "longitude", "valueDecimal": -89.40},
                            ],
                        }
                    ],
                }
            ],
            "extension": [
                {
                    "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race",
                    "extension": [
                        {"url": "text", "valueString": "Asian"},
                    ],
                },
                {
                    "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity",
                    "extension": [
                        {"url": "text", "valueString": "Not Hispanic or Latino"},
                    ],
                },
            ],
        }
        result = _parse_patient(resource)
        pid, birth, gender, race, ethnicity, city, state, lat, lng = result
        assert pid == "epic-p1"
        assert birth == "1980-05-12"
        assert gender == "male"
        assert race == "Asian"
        assert ethnicity == "Not Hispanic or Latino"
        assert city == "Madison"
        assert state == "WI"
        assert lat == 43.07
        assert lng == -89.40


class TestEpicEncounter:
    def test_full_url_subject_reference(self):
        """Epic uses full URL references like https://epic.server/fhir/Patient/abc123."""
        resource = {
            "resourceType": "Encounter",
            "id": "enc-1",
            "subject": {"reference": "https://epic.server/fhir/Patient/abc123"},
            "class": {"code": "IMP"},
            "period": {"start": "2025-01-01", "end": "2025-01-03"},
        }
        result = _parse_encounter(resource)
        enc_id, patient_id, enc_type, start, end, *_ = result
        assert enc_id == "enc-1"
        assert patient_id == "abc123"
        assert enc_type == "inpatient"

    def test_reason_reference_instead_of_reason_code(self):
        """Epic may use reasonReference instead of reasonCode.
        The parser should not crash; reason fields will be None."""
        resource = {
            "resourceType": "Encounter",
            "id": "enc-2",
            "subject": {"reference": "Patient/p1"},
            "class": {"code": "AMB"},
            "period": {"start": "2025-02-01"},
            "reasonReference": [{"reference": "Condition/c1"}],
            # no reasonCode at all
        }
        result = _parse_encounter(resource)
        enc_id, patient_id, enc_type, start, end, reason_code, reason_display, discharge = result
        assert enc_id == "enc-2"
        assert enc_type == "ambulatory"
        # reasonReference is resolved: code is the referenced resource ID
        assert reason_code == "c1"
        assert reason_display is None


class TestEpicMedicationRequest:
    def test_medication_codeable_concept(self):
        """Standard medicationCodeableConcept should parse normally."""
        resource = {
            "resourceType": "MedicationRequest",
            "id": "med-1",
            "subject": {"reference": "Patient/p1"},
            "encounter": {"reference": "Encounter/e1"},
            "medicationCodeableConcept": {
                "coding": [{"code": "1049221", "display": "Lisinopril 10mg"}],
            },
            "authoredOn": "2025-01-02",
            "status": "active",
        }
        result = _parse_medication(resource)
        med_id, pat, enc, code, display, authored, end, status = result
        assert med_id == "med-1"
        assert display == "Lisinopril 10mg"
        assert code == "1049221"

    def test_medication_reference_fallback(self):
        """Epic may use medicationReference instead of medicationCodeableConcept.
        Current parser returns (None, None) for the code/display — verify it
        does not crash."""
        resource = {
            "resourceType": "MedicationRequest",
            "id": "med-2",
            "subject": {"reference": "Patient/p1"},
            "encounter": {"reference": "Encounter/e1"},
            "medicationReference": {
                "reference": "Medication/med1",
                "display": "Lisinopril 10mg",
            },
            "authoredOn": "2025-01-02",
            "status": "active",
        }
        result = _parse_medication(resource)
        med_id, pat, enc, code, display, authored, end, status = result
        assert med_id == "med-2"
        # Falls back to medicationReference: code is the ref ID, display from reference
        assert code == "med1"
        assert display == "Lisinopril 10mg"


class TestEpicObservation:
    def test_component_observation(self):
        """Blood pressure observation with component array.
        Parser should not crash even though it only reads top-level value."""
        resource = {
            "resourceType": "Observation",
            "id": "obs-bp",
            "subject": {"reference": "Patient/p1"},
            "encounter": {"reference": "Encounter/e1"},
            "code": {
                "coding": [{"code": "85354-9", "display": "Blood pressure panel"}],
            },
            "effectiveDateTime": "2025-01-01T10:00:00Z",
            "component": [
                {
                    "code": {"coding": [{"code": "8480-6", "display": "Systolic"}]},
                    "valueQuantity": {"value": 120, "unit": "mmHg"},
                },
                {
                    "code": {"coding": [{"code": "8462-4", "display": "Diastolic"}]},
                    "valueQuantity": {"value": 80, "unit": "mmHg"},
                },
            ],
        }
        result = _parse_observation(resource)
        obs_id, pat, enc, code, display, value, unit, effective = result
        assert obs_id == "obs-bp"
        assert code == "85354-9"
        assert display == "Blood pressure panel"
        # Component values are concatenated into a single string
        assert value == "Systolic: 120 mmHg; Diastolic: 80 mmHg"
        assert unit is None

    def test_simple_observation(self):
        resource = {
            "resourceType": "Observation",
            "id": "obs-1",
            "subject": {"reference": "Patient/p1"},
            "encounter": {"reference": "Encounter/e1"},
            "code": {
                "coding": [{"code": "29463-7", "display": "Body Weight"}],
            },
            "valueQuantity": {"value": 85.5, "unit": "kg"},
            "effectiveDateTime": "2025-03-01T08:00:00Z",
        }
        result = _parse_observation(resource)
        obs_id, pat, enc, code, display, value, unit, effective = result
        assert value == "85.5"
        assert unit == "kg"
