"""MIMIC-III BigQuery source.

Reads MIMIC-III tables from BigQuery (`physionet-data.mimiciii_clinical` and
`physionet-data.mimiciii_notes`), translates rows into FHIR R4 resource dicts,
and yields them through the same ``FhirSource`` protocol used for Synthea and
live FHIR APIs. Downstream ingest, identify-pairs, review, and analytics work
unchanged.

ID convention
-------------
All emitted resource ids are namespaced with ``m3-`` so they cannot collide
with Synthea UUIDs or FHIR-API ids if a database happens to be mixed:

    Patient            m3-p<subject_id>           e.g. m3-p109
    Encounter          m3-a<hadm_id>              e.g. m3-a183350
    Condition          m3-dx-<hadm_id>-<seq_num>
    Procedure          m3-px-<hadm_id>-<seq_num>
    MedicationRequest  m3-rx-<prescriptions.row_id>
    Observation (lab)  m3-lab-<labevents.row_id>

The prefix is reversible — strip ``m3-p`` / ``m3-a`` to recover the raw
subject_id / hadm_id used by ``mimic-ehr`` for chart deep-links.

Data Use Agreement
------------------
MIMIC-III is PhysioNet *restricted* data under DUA v1.5. The credentialed user
is responsible for keeping ingested data in restricted infrastructure (no
public deployment, no shared DB credentials). See the project DATA.md.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterator, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ICD9_CM = "http://hl7.org/fhir/sid/icd-9-cm"
ICD9_PCS = "http://hl7.org/fhir/sid/icd-9-cm-procedure"
LOINC = "http://loinc.org"

DATASET_CLINICAL = "physionet-data.mimiciii_clinical"
DATASET_NOTES = "physionet-data.mimiciii_notes"

# Map MIMIC-III `admissions.discharge_location` values to phrases that
# `pairs.py` transfer-disposition patterns can match
# (skilled nursing, transferred to, rehabilitation, hospice, long term care, snf).
_DISCHARGE_MAP: dict[str, str] = {
    "HOME": "Home",
    "HOME WITH HOME IV PROVIDR": "Home with Home IV Provider",
    "HOME HEALTH CARE": "Home Health Care",
    "SNF": "Skilled Nursing Facility (SNF)",
    "SNF-MEDICAID ONLY CERTIF": "Skilled Nursing Facility (SNF)",
    "REHAB/DISTINCT PART HOSP": "Rehabilitation Facility",
    "LONG TERM CARE HOSPITAL": "Long Term Care Hospital",
    "HOSPICE-HOME": "Hospice (Home)",
    "HOSPICE-MEDICAL FACILITY": "Hospice (Medical Facility)",
    "DEAD/EXPIRED": "Expired",
    "LEFT AGAINST MEDICAL ADVI": "Left Against Medical Advice",
    "DISC-TRAN CANCER/CHLDRN H": "Transferred to Cancer/Children's Hospital",
    "DISC-TRAN TO FEDERAL HC": "Transferred to Federal Healthcare Facility",
    "DISCH-TRAN TO PSYCH HOSP": "Transferred to Psychiatric Hospital",
    "OTHER FACILITY": "Other Facility",
    "ICF": "Intermediate Care Facility",
    "SHORT TERM HOSPITAL": "Short Term Hospital",
}


def _normalize_discharge(loc: str | None) -> str | None:
    if loc is None:
        return None
    return _DISCHARGE_MAP.get(loc.strip(), loc.strip().title())


def _ts(v) -> str | None:
    """Stringify a BigQuery datetime/timestamp value."""
    if v is None:
        return None
    return str(v)


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

ALL_RESOURCE_TYPES = (
    "Patient",
    "Encounter",
    "Condition",
    "Procedure",
    "MedicationRequest",
    "Observation",
    "Note",
)


@dataclass
class MimicBigQuerySource:
    """A FhirSource backed by MIMIC-III tables in BigQuery."""

    project_id: str
    cohort_size: int = 100
    cohort_strategy: str = "readmit"   # "readmit" | "high-acuity" | "random"
    include_labs: bool = True
    include_notes: bool = True
    # If set, only emit resources whose type is in this set. Lets a second
    # ingest pass backfill a single resource type (e.g. {"Note"}) without
    # re-scanning labs/meds. None = emit everything that is also enabled
    # by the include_* flags above.
    resource_filter: frozenset[str] | None = None
    dataset_clinical: str = DATASET_CLINICAL
    dataset_notes: str = DATASET_NOTES
    explicit_subject_ids: Sequence[int] | None = None
    _bytes_billed: int = field(default=0, init=False, repr=False)

    def _enabled(self, rtype: str) -> bool:
        if self.resource_filter is not None:
            return rtype in self.resource_filter
        if rtype == "Observation":
            return self.include_labs
        if rtype == "Note":
            return self.include_notes
        return True

    # ---- BigQuery client -------------------------------------------------

    def _client(self):
        # Imported lazily so the rest of the codebase doesn't require
        # google-cloud-bigquery unless this source is actually used.
        try:
            from google.cloud import bigquery  # type: ignore[import-not-found]
            from google.auth import default as default_auth  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "google-cloud-bigquery is not installed. "
                "Install with: pip install 'google-cloud-bigquery>=3.25.0'"
            ) from e

        creds, _ = default_auth(scopes=["https://www.googleapis.com/auth/bigquery"])
        # Override the ADC quota project: the global gcloud install may have
        # `quota_project_id` set to a different project the active account
        # cannot bill against, which would 500 every query with
        # USER_PROJECT_DENIED.
        if hasattr(creds, "with_quota_project"):
            creds = creds.with_quota_project(self.project_id)
        return bigquery.Client(project=self.project_id, credentials=creds)

    def _query(self, sql: str, params: dict | None = None) -> Iterator[dict]:
        from google.cloud import bigquery  # type: ignore[import-not-found]

        job_config = bigquery.QueryJobConfig(
            query_parameters=_to_bq_params(params or {}),
        )
        job = self._client().query(sql, job_config=job_config)
        rows = list(job.result())
        if job.total_bytes_billed:
            self._bytes_billed += int(job.total_bytes_billed)
        for r in rows:
            yield dict(r.items())

    # ---- Cohort selection ------------------------------------------------

    def _select_cohort(self) -> list[int]:
        if self.explicit_subject_ids:
            return list(self.explicit_subject_ids)

        if self.cohort_strategy == "readmit":
            sql = f"""
                WITH ranked AS (
                  SELECT subject_id, dischtime,
                         LEAD(admittime) OVER (
                           PARTITION BY subject_id ORDER BY admittime
                         ) AS next_admittime
                  FROM `{self.dataset_clinical}.admissions`
                )
                SELECT DISTINCT subject_id
                FROM ranked
                WHERE TIMESTAMP_DIFF(next_admittime, dischtime, DAY) BETWEEN 0 AND 30
                ORDER BY subject_id
                LIMIT @n
            """
        elif self.cohort_strategy == "high-acuity":
            sql = f"""
                SELECT subject_id FROM (
                  SELECT subject_id, COUNT(*) AS n_adm
                  FROM `{self.dataset_clinical}.admissions`
                  GROUP BY subject_id
                )
                ORDER BY n_adm DESC, subject_id
                LIMIT @n
            """
        elif self.cohort_strategy == "random":
            sql = f"""
                SELECT subject_id
                FROM `{self.dataset_clinical}.patients`
                ORDER BY FARM_FINGERPRINT(CAST(subject_id AS STRING))
                LIMIT @n
            """
        else:
            raise ValueError(f"Unknown cohort_strategy: {self.cohort_strategy}")

        ids = [int(row["subject_id"]) for row in self._query(sql, {"n": self.cohort_size})]
        logger.info(
            "Selected cohort: %d patients (strategy=%s, requested=%d)",
            len(ids), self.cohort_strategy, self.cohort_size,
        )
        return ids

    # ---- iter_resources --------------------------------------------------

    def iter_resources(self) -> Iterator[dict]:
        """Stream FHIR resource dicts shaped for ``ingest._parse_*``."""
        cohort = self._select_cohort()
        if not cohort:
            logger.warning("Empty cohort — nothing to yield.")
            return

        n = 0

        # Patient ethnicity is per-admission in MIMIC; pick the most recent
        # admission's value as the patient-level value. Skip the lookup if
        # we're not emitting Patients this pass.
        if self._enabled("Patient"):
            patient_ethnicity = self._latest_ethnicities(cohort)
            for r in self._iter_patients(cohort, patient_ethnicity):
                yield r
                n += 1

        if self._enabled("Encounter"):
            for r in self._iter_encounters(cohort):
                yield r
                n += 1
        if self._enabled("Condition"):
            for r in self._iter_conditions(cohort):
                yield r
                n += 1
        if self._enabled("Procedure"):
            for r in self._iter_procedures(cohort):
                yield r
                n += 1
        if self._enabled("MedicationRequest"):
            for r in self._iter_medications(cohort):
                yield r
                n += 1
        if self._enabled("Observation"):
            for r in self._iter_labs(cohort):
                yield r
                n += 1
        if self._enabled("Note"):
            for r in self._iter_notes(cohort):
                yield r
                n += 1

        gb = self._bytes_billed / 1e9
        logger.info(
            "MIMIC ingest: %d resources yielded, ~%.2f GB billed across queries.",
            n, gb,
        )

    # ---- Resource builders ----------------------------------------------

    def _latest_ethnicities(self, cohort: list[int]) -> dict[int, str | None]:
        sql = f"""
            SELECT subject_id, ethnicity
            FROM (
              SELECT subject_id, ethnicity,
                     ROW_NUMBER() OVER (
                       PARTITION BY subject_id ORDER BY admittime DESC
                     ) AS rn
              FROM `{self.dataset_clinical}.admissions`
              WHERE subject_id IN UNNEST(@subjects)
            )
            WHERE rn = 1
        """
        out: dict[int, str | None] = {}
        for row in self._query(sql, {"subjects": cohort}):
            out[int(row["subject_id"])] = row.get("ethnicity")
        return out

    def _iter_patients(
        self, cohort: list[int], ethnicities: dict[int, str | None]
    ) -> Iterator[dict]:
        sql = f"""
            SELECT subject_id, gender,
                   CAST(dob AS STRING) AS dob,
                   CAST(dod AS STRING) AS dod
            FROM `{self.dataset_clinical}.patients`
            WHERE subject_id IN UNNEST(@subjects)
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            gender = (row.get("gender") or "").upper()
            gender_norm = {"M": "male", "F": "female"}.get(gender)
            ethnicity = ethnicities.get(sid)
            extension = []
            if ethnicity:
                extension.append({
                    "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity",
                    "extension": [{"url": "text", "valueString": ethnicity}],
                })
            patient: dict = {
                "resourceType": "Patient",
                "id": f"m3-p{sid}",
                "gender": gender_norm,
                # MIMIC DOBs are date-shifted (and >=89 are 300y-shifted) but
                # remain ISO-formatted, so feed straight through.
                "birthDate": (row.get("dob") or "")[:10] or None,
                "extension": extension,
                "address": [{}],
            }
            if row.get("dod"):
                patient["deceasedDateTime"] = _ts(row["dod"])
            yield patient

    def _iter_encounters(self, cohort: list[int]) -> Iterator[dict]:
        # Reason = principal ICD-9 diagnosis (seq_num=1) joined to its
        # short_title. Falls back to the free-text admission diagnosis.
        sql = f"""
            WITH principal AS (
              SELECT d.hadm_id, d.icd9_code, ref.short_title
              FROM `{self.dataset_clinical}.diagnoses_icd` d
              LEFT JOIN `{self.dataset_clinical}.d_icd_diagnoses` ref
                ON d.icd9_code = ref.icd9_code
              WHERE d.seq_num = 1
            )
            SELECT a.hadm_id, a.subject_id,
                   CAST(a.admittime AS STRING) AS admittime,
                   CAST(a.dischtime AS STRING) AS dischtime,
                   a.admission_type, a.discharge_location, a.diagnosis,
                   a.hospital_expire_flag,
                   p.icd9_code AS principal_icd9,
                   p.short_title AS principal_title
            FROM `{self.dataset_clinical}.admissions` a
            LEFT JOIN principal p USING (hadm_id)
            WHERE a.subject_id IN UNNEST(@subjects)
            ORDER BY a.subject_id, a.admittime
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = int(row["hadm_id"])
            admit = _ts(row.get("admittime"))
            disch = _ts(row.get("dischtime"))
            principal_code = row.get("principal_icd9")
            principal_title = row.get("principal_title")
            free_text = row.get("diagnosis")

            reason: dict = {}
            if principal_code:
                reason["coding"] = [{
                    "system": ICD9_CM,
                    "code": principal_code,
                    "display": principal_title or free_text,
                }]
            reason["text"] = principal_title or free_text

            disposition = _normalize_discharge(row.get("discharge_location"))
            hospitalization: dict = {}
            if disposition:
                hospitalization = {
                    "dischargeDisposition": {
                        "coding": [{"display": disposition}],
                        "text": disposition,
                    }
                }

            # MIMIC NEWBORN admissions are still inpatient encounters; the
            # readmission cohort filter excludes newborns elsewhere, but here
            # we keep them flagged via admission_type for downstream inspection.
            enc: dict = {
                "resourceType": "Encounter",
                "id": f"m3-a{hid}",
                "subject": {"reference": f"Patient/m3-p{sid}"},
                "class": {"code": "IMP", "display": "inpatient"},
                "period": {"start": admit, "end": disch},
                "type": [{"text": (row.get("admission_type") or "").title() or None}],
                "reasonCode": [reason] if reason else [],
                "hospitalization": hospitalization,
            }
            yield enc

    def _iter_conditions(self, cohort: list[int]) -> Iterator[dict]:
        sql = f"""
            SELECT d.subject_id, d.hadm_id, d.seq_num, d.icd9_code,
                   ref.short_title, ref.long_title,
                   CAST(a.admittime AS STRING) AS admittime
            FROM `{self.dataset_clinical}.diagnoses_icd` d
            LEFT JOIN `{self.dataset_clinical}.d_icd_diagnoses` ref
              ON d.icd9_code = ref.icd9_code
            LEFT JOIN `{self.dataset_clinical}.admissions` a
              ON d.hadm_id = a.hadm_id
            WHERE d.subject_id IN UNNEST(@subjects)
              AND d.icd9_code IS NOT NULL
            ORDER BY d.subject_id, d.hadm_id, d.seq_num
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = int(row["hadm_id"])
            seq = row.get("seq_num")
            code = row["icd9_code"]
            display = row.get("short_title") or row.get("long_title")
            yield {
                "resourceType": "Condition",
                "id": f"m3-dx-{hid}-{seq}",
                "subject": {"reference": f"Patient/m3-p{sid}"},
                "encounter": {"reference": f"Encounter/m3-a{hid}"},
                "code": {
                    "coding": [{
                        "system": ICD9_CM,
                        "code": code,
                        "display": display,
                    }],
                    "text": display,
                },
                "onsetDateTime": _ts(row.get("admittime")),
                "clinicalStatus": {"coding": [{"code": "resolved"}]},
            }

    def _iter_procedures(self, cohort: list[int]) -> Iterator[dict]:
        sql = f"""
            SELECT p.subject_id, p.hadm_id, p.seq_num, p.icd9_code,
                   ref.short_title, ref.long_title,
                   CAST(a.admittime AS STRING) AS admittime
            FROM `{self.dataset_clinical}.procedures_icd` p
            LEFT JOIN `{self.dataset_clinical}.d_icd_procedures` ref
              ON p.icd9_code = ref.icd9_code
            LEFT JOIN `{self.dataset_clinical}.admissions` a
              ON p.hadm_id = a.hadm_id
            WHERE p.subject_id IN UNNEST(@subjects)
              AND p.icd9_code IS NOT NULL
            ORDER BY p.subject_id, p.hadm_id, p.seq_num
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = int(row["hadm_id"])
            seq = row.get("seq_num")
            code = row["icd9_code"]
            display = row.get("short_title") or row.get("long_title")
            yield {
                "resourceType": "Procedure",
                "id": f"m3-px-{hid}-{seq}",
                "subject": {"reference": f"Patient/m3-p{sid}"},
                "encounter": {"reference": f"Encounter/m3-a{hid}"},
                "code": {
                    "coding": [{
                        "system": ICD9_PCS,
                        "code": code,
                        "display": display,
                    }],
                    "text": display,
                },
                "performedDateTime": _ts(row.get("admittime")),
                "status": "completed",
            }

    def _iter_medications(self, cohort: list[int]) -> Iterator[dict]:
        sql = f"""
            SELECT row_id, subject_id, hadm_id,
                   CAST(startdate AS STRING) AS startdate,
                   drug, drug_name_generic, formulary_drug_cd,
                   prod_strength, dose_val_rx, dose_unit_rx, route
            FROM `{self.dataset_clinical}.prescriptions`
            WHERE subject_id IN UNNEST(@subjects)
              AND drug IS NOT NULL
            ORDER BY subject_id, hadm_id, startdate, drug
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = row.get("hadm_id")
            rid = int(row["row_id"])
            display = row.get("drug")
            if row.get("prod_strength"):
                display = f"{display} {row['prod_strength']}"
            elif row.get("dose_val_rx"):
                display = f"{display} {row['dose_val_rx']} {row.get('dose_unit_rx') or ''}".strip()
            yield {
                "resourceType": "MedicationRequest",
                "id": f"m3-rx-{rid}",
                "subject": {"reference": f"Patient/m3-p{sid}"},
                "encounter": (
                    {"reference": f"Encounter/m3-a{int(hid)}"} if hid is not None else {}
                ),
                "medicationCodeableConcept": {
                    "coding": (
                        [{"code": row["formulary_drug_cd"], "display": display}]
                        if row.get("formulary_drug_cd") else []
                    ),
                    "text": display,
                },
                "authoredOn": (row.get("startdate") or "")[:10] or None,
                "status": "completed",
            }

    def _iter_notes(self, cohort: list[int]) -> Iterator[dict]:
        # Notes scoped to the patient's admissions. iserror filters out the
        # ~880 entries flagged as charting errors. category/description are
        # MIMIC's own taxonomy (Discharge summary, Physician, Nursing, etc.);
        # we keep them as-is so downstream filters can match by category.
        sql = f"""
            SELECT row_id, subject_id, hadm_id,
                   CAST(chartdate AS STRING) AS chartdate,
                   CAST(charttime AS STRING) AS charttime,
                   category, description, text
            FROM `{self.dataset_notes}.noteevents`
            WHERE subject_id IN UNNEST(@subjects)
              AND hadm_id IS NOT NULL
              AND (iserror IS NULL OR iserror != 1)
            ORDER BY subject_id, hadm_id, COALESCE(charttime, chartdate)
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = int(row["hadm_id"])
            rid = int(row["row_id"])
            yield {
                "resourceType": "Note",
                "id": f"m3-note-{rid}",
                "subject": {"reference": f"Patient/m3-p{sid}"},
                "encounter": {"reference": f"Encounter/m3-a{hid}"},
                "category": (row.get("category") or "").strip() or None,
                "description": (row.get("description") or "").strip() or None,
                "chartdate": row.get("chartdate"),
                "charttime": row.get("charttime"),
                "text": row.get("text"),
            }

    def _iter_labs(self, cohort: list[int]) -> Iterator[dict]:
        # Filter to admission-scoped labs (hadm_id IS NOT NULL) so the
        # ingest's encounter FK isn't violated. Outpatient labs (no hadm_id)
        # are dropped — the agent's clinical context is admission-centric.
        sql = f"""
            SELECT le.row_id, le.subject_id, le.hadm_id, le.itemid,
                   CAST(le.charttime AS STRING) AS charttime,
                   le.value, le.valuenum, le.valueuom, le.flag,
                   d.label, d.fluid, d.category, d.loinc_code
            FROM `{self.dataset_clinical}.labevents` le
            LEFT JOIN `{self.dataset_clinical}.d_labitems` d USING (itemid)
            WHERE le.subject_id IN UNNEST(@subjects)
              AND le.hadm_id IS NOT NULL
            ORDER BY le.subject_id, le.hadm_id, le.charttime
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = int(row["hadm_id"])
            rid = int(row["row_id"])
            label = row.get("label") or f"itemid {row['itemid']}"
            loinc = row.get("loinc_code")
            coding = []
            if loinc:
                coding.append({
                    "system": LOINC,
                    "code": loinc,
                    "display": label,
                })
            obs: dict = {
                "resourceType": "Observation",
                "id": f"m3-lab-{rid}",
                "subject": {"reference": f"Patient/m3-p{sid}"},
                "encounter": {"reference": f"Encounter/m3-a{hid}"},
                "code": {"coding": coding, "text": label},
                "effectiveDateTime": _ts(row.get("charttime")),
                "category": [{"text": row.get("category") or "laboratory"}],
            }
            if row.get("valuenum") is not None:
                obs["valueQuantity"] = {
                    "value": float(row["valuenum"]),
                    "unit": row.get("valueuom"),
                }
            elif row.get("value") is not None:
                obs["valueCodeableConcept"] = {"text": str(row["value"])}
            if row.get("flag"):
                obs["interpretation"] = [{"text": row["flag"]}]
            yield obs


# ---------------------------------------------------------------------------
# BigQuery parameter conversion
# ---------------------------------------------------------------------------

def _to_bq_params(params: dict):
    from google.cloud import bigquery  # type: ignore[import-not-found]

    out = []
    for name, value in params.items():
        if isinstance(value, bool):
            out.append(bigquery.ScalarQueryParameter(name, "BOOL", value))
        elif isinstance(value, int):
            out.append(bigquery.ScalarQueryParameter(name, "INT64", value))
        elif isinstance(value, float):
            out.append(bigquery.ScalarQueryParameter(name, "FLOAT64", value))
        elif isinstance(value, str):
            out.append(bigquery.ScalarQueryParameter(name, "STRING", value))
        elif isinstance(value, (list, tuple)):
            sample = next((x for x in value if x is not None), None)
            if isinstance(sample, int):
                t = "INT64"
            elif isinstance(sample, float):
                t = "FLOAT64"
            else:
                t = "STRING"
            out.append(bigquery.ArrayQueryParameter(name, t, list(value)))
        else:
            raise TypeError(f"Unsupported BQ param type for {name}: {type(value)}")
    return out


# ---------------------------------------------------------------------------
# Convenience: build a source from environment / CLI args
# ---------------------------------------------------------------------------

def make_source_from_env(
    *,
    cohort_size: int | None = None,
    cohort_strategy: str | None = None,
    include_labs: bool = True,
    project_id: str | None = None,
) -> MimicBigQuerySource:
    """Build a configured MimicBigQuerySource using env vars as defaults."""
    pid = project_id or os.environ.get("BQ_PROJECT_ID")
    if not pid:
        raise RuntimeError(
            "BQ_PROJECT_ID is not set. Pass --bq-project or export BQ_PROJECT_ID="
            "<your-gcp-project>."
        )
    return MimicBigQuerySource(
        project_id=pid,
        cohort_size=cohort_size if cohort_size is not None else int(
            os.environ.get("MIMIC_COHORT_SIZE", "100")
        ),
        cohort_strategy=cohort_strategy or os.environ.get(
            "MIMIC_COHORT_STRATEGY", "readmit"
        ),
        include_labs=include_labs,
    )
