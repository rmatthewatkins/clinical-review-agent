"""MIMIC-IV BigQuery source.

Reads MIMIC-IV tables from BigQuery (`physionet-data.mimiciv_hosp` and
`physionet-data.mimiciv_note`), translates rows into FHIR R4 resource dicts,
and yields them through the same ``FhirSource`` protocol used for Synthea,
live FHIR APIs, and MIMIC-III. Downstream ingest, identify, review, and
analytics work unchanged.

Differences from MIMIC-III
--------------------------
- Datasets: ``mimiciv_hosp`` (clinical) and ``mimiciv_note`` (notes), both
  on the public ``physionet-data`` GCP project.
- Patients have no ``dob`` — only ``anchor_age`` + ``anchor_year``. We
  synthesize ``birthDate`` as ``{anchor_year - anchor_age}-01-01``. Years
  are date-shifted in MIMIC, so this is intentionally year-only fidelity.
- Demographics column is ``race`` (per-admission) instead of ``ethnicity``.
- Diagnoses/procedures carry an ``icd_version`` (9 or 10); we set the FHIR
  coding system accordingly.
- ``d_labitems`` no longer has ``loinc_code`` — lab Observations have
  ``code.text`` only (no LOINC coding).
- Notes are split across ``discharge`` and ``radiology`` tables (instead of a
  single ``noteevents``). We map ``note_type`` ('DS', 'RR') back to the
  category strings the agent's key-note filter recognises ("Discharge
  summary", "Radiology"). Physician/H&P notes aren't published in MIMIC-IV.
- Prescriptions have no ``row_id``; we hash the natural key with MD5 to get
  a stable resource id.

ID convention
-------------
All emitted resource ids are namespaced with ``m4-`` so they cannot collide
with Synthea UUIDs, FHIR-API ids, or MIMIC-III (``m3-``) ids:

    Patient            m4-p<subject_id>
    Encounter          m4-a<hadm_id>
    Condition          m4-dx-<hadm_id>-<seq_num>
    Procedure          m4-px-<hadm_id>-<seq_num>
    MedicationRequest  m4-rx-<md5(natural-key)>
    Observation (lab)  m4-lab-<labevent_id>
    Note               m4-note-<note_id>     (note_id is already a string)

Data Use Agreement
------------------
MIMIC-IV is PhysioNet *restricted* data under DUA v1.5. The credentialed user
is responsible for keeping ingested data in restricted infrastructure (no
public deployment, no shared DB credentials). See the project DATA.md.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Iterator, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ICD9_CM = "http://hl7.org/fhir/sid/icd-9-cm"
ICD10_CM = "http://hl7.org/fhir/sid/icd-10-cm"
ICD9_PCS = "http://hl7.org/fhir/sid/icd-9-cm-procedure"
ICD10_PCS = "http://www.cms.gov/Medicare/Coding/ICD10"

DATASET_HOSP = "physionet-data.mimiciv_3_1_hosp"
DATASET_NOTE = "physionet-data.mimiciv_note"
# PhysioNet ships MIMIC-IV under versioned dataset names (e.g.
# `mimiciv_3_1_hosp`) and, for some credentialed projects, an unversioned
# alias (`mimiciv_hosp`). The aliases require a separate access grant, so
# the safer default targets the versioned names. Override at the call site
# (CLI: --mimic-iv-version) to pin to a different release.

# Map MIMIC-IV `admissions.discharge_location` values to phrases the
# `pairs.py` transfer-disposition matcher and `mortality.py` death-disposition
# matcher already understand (skilled nursing, transferred to, rehabilitation,
# hospice, long term care, snf, expired, died, deceased).
_DISCHARGE_MAP: dict[str, str] = {
    "HOME": "Home",
    "HOME HEALTH CARE": "Home Health Care",
    "SKILLED NURSING FACILITY": "Skilled Nursing Facility (SNF)",
    "CHRONIC/LONG TERM ACUTE CARE": "Long Term Care Hospital",
    "REHAB": "Rehabilitation Facility",
    "ACUTE HOSPITAL": "Transferred to Acute Hospital",
    "PSYCH FACILITY": "Transferred to Psychiatric Facility",
    "OTHER FACILITY": "Other Facility",
    "HOSPICE": "Hospice",
    "AGAINST ADVICE": "Left Against Medical Advice",
    "ASSISTED LIVING": "Assisted Living",
    "HEALTHCARE FACILITY": "Transferred to Healthcare Facility",
    "DIED": "Expired",
}

# MIMIC-IV note_type -> category string the agent recognises.
_NOTE_TYPE_MAP: dict[str, str] = {
    "DS": "Discharge summary",
    "RR": "Radiology",
}


def _normalize_discharge(loc: str | None) -> str | None:
    if loc is None:
        return None
    s = loc.strip()
    if not s:
        return None
    return _DISCHARGE_MAP.get(s.upper(), s.title())


def _ts(v) -> str | None:
    """Stringify a BigQuery datetime/timestamp value."""
    if v is None:
        return None
    return str(v)


def _icd_dx_system(version) -> str:
    """ICD diagnosis code system URI for the given icd_version (9 or 10)."""
    return ICD10_CM if str(version).strip() == "10" else ICD9_CM


def _icd_px_system(version) -> str:
    """ICD procedure code system URI for the given icd_version (9 or 10)."""
    return ICD10_PCS if str(version).strip() == "10" else ICD9_PCS


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
class MimicIvBigQuerySource:
    """A FhirSource backed by MIMIC-IV tables in BigQuery."""

    project_id: str
    cohort_size: int = 100
    cohort_strategy: str = "readmit"   # "readmit" | "high-acuity" | "random"
    include_labs: bool = True
    include_notes: bool = True
    # If True, also emit radiology reports alongside discharge summaries.
    # Off by default — radiology reports are voluminous and the agent's
    # key-note filter only reads discharge summaries today.
    include_radiology: bool = False
    # If set, only emit resources whose type is in this set. Lets a second
    # ingest pass backfill a single resource type (e.g. {"Note"}) without
    # re-scanning labs/meds. None = emit everything that is also enabled
    # by the include_* flags above.
    resource_filter: frozenset[str] | None = None
    dataset_hosp: str = DATASET_HOSP
    dataset_note: str = DATASET_NOTE
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
                  FROM `{self.dataset_hosp}.admissions`
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
                  FROM `{self.dataset_hosp}.admissions`
                  GROUP BY subject_id
                )
                ORDER BY n_adm DESC, subject_id
                LIMIT @n
            """
        elif self.cohort_strategy == "random":
            sql = f"""
                SELECT subject_id
                FROM `{self.dataset_hosp}.patients`
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

        # Race is per-admission in MIMIC-IV; pick the most recent admission's
        # value as the patient-level value. Skip the lookup if we're not
        # emitting Patients this pass.
        if self._enabled("Patient"):
            patient_race = self._latest_races(cohort)
            for r in self._iter_patients(cohort, patient_race):
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
            "MIMIC-IV ingest: %d resources yielded, ~%.2f GB billed across queries.",
            n, gb,
        )

    # ---- Resource builders ----------------------------------------------

    def _latest_races(self, cohort: list[int]) -> dict[int, str | None]:
        sql = f"""
            SELECT subject_id, race
            FROM (
              SELECT subject_id, race,
                     ROW_NUMBER() OVER (
                       PARTITION BY subject_id ORDER BY admittime DESC
                     ) AS rn
              FROM `{self.dataset_hosp}.admissions`
              WHERE subject_id IN UNNEST(@subjects)
            )
            WHERE rn = 1
        """
        out: dict[int, str | None] = {}
        for row in self._query(sql, {"subjects": cohort}):
            out[int(row["subject_id"])] = row.get("race")
        return out

    def _iter_patients(
        self, cohort: list[int], races: dict[int, str | None]
    ) -> Iterator[dict]:
        # MIMIC-IV has no DOB. Birth year ≈ anchor_year - anchor_age. Years
        # are date-shifted (anchor_year_group spans 3-year windows) so we
        # only carry year-level fidelity — pin to Jan 1.
        sql = f"""
            SELECT subject_id, gender, anchor_age, anchor_year,
                   anchor_year_group,
                   CAST(dod AS STRING) AS dod
            FROM `{self.dataset_hosp}.patients`
            WHERE subject_id IN UNNEST(@subjects)
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            gender = (row.get("gender") or "").upper()
            gender_norm = {"M": "male", "F": "female"}.get(gender)
            race = races.get(sid)
            extension = []
            if race:
                extension.append({
                    "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race",
                    "extension": [{"url": "text", "valueString": race}],
                })

            anchor_age = row.get("anchor_age")
            anchor_year = row.get("anchor_year")
            birth_date = None
            if anchor_age is not None and anchor_year is not None:
                try:
                    birth_year = int(anchor_year) - int(anchor_age)
                    birth_date = f"{birth_year:04d}-01-01"
                except (ValueError, TypeError):
                    birth_date = None

            patient: dict = {
                "resourceType": "Patient",
                "id": f"m4-p{sid}",
                "gender": gender_norm,
                "birthDate": birth_date,
                "extension": extension,
                "address": [{}],
            }
            if row.get("dod"):
                patient["deceasedDateTime"] = _ts(row["dod"])
            yield patient

    def _iter_encounters(self, cohort: list[int]) -> Iterator[dict]:
        # Reason = principal ICD diagnosis (seq_num=1) joined to its long_title.
        # MIMIC-IV admissions table does NOT carry the free-text `diagnosis`
        # column anymore — the principal ICD is the only reason signal.
        sql = f"""
            WITH principal AS (
              SELECT d.hadm_id, d.icd_code, d.icd_version, ref.long_title
              FROM `{self.dataset_hosp}.diagnoses_icd` d
              LEFT JOIN `{self.dataset_hosp}.d_icd_diagnoses` ref
                ON d.icd_code = ref.icd_code
                AND d.icd_version = ref.icd_version
              WHERE d.seq_num = 1
            )
            SELECT a.hadm_id, a.subject_id,
                   CAST(a.admittime AS STRING) AS admittime,
                   CAST(a.dischtime AS STRING) AS dischtime,
                   a.admission_type, a.discharge_location,
                   a.hospital_expire_flag,
                   p.icd_code AS principal_icd,
                   p.icd_version AS principal_icd_version,
                   p.long_title AS principal_title
            FROM `{self.dataset_hosp}.admissions` a
            LEFT JOIN principal p USING (hadm_id)
            WHERE a.subject_id IN UNNEST(@subjects)
            ORDER BY a.subject_id, a.admittime
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = int(row["hadm_id"])
            admit = _ts(row.get("admittime"))
            disch = _ts(row.get("dischtime"))
            principal_code = row.get("principal_icd")
            principal_version = row.get("principal_icd_version")
            principal_title = row.get("principal_title")

            reason: dict = {}
            if principal_code:
                reason["coding"] = [{
                    "system": _icd_dx_system(principal_version),
                    "code": principal_code,
                    "display": principal_title,
                }]
            if principal_title:
                reason["text"] = principal_title

            disposition = _normalize_discharge(row.get("discharge_location"))
            hospitalization: dict = {}
            if disposition:
                hospitalization = {
                    "dischargeDisposition": {
                        "coding": [{"display": disposition}],
                        "text": disposition,
                    }
                }

            enc: dict = {
                "resourceType": "Encounter",
                "id": f"m4-a{hid}",
                "subject": {"reference": f"Patient/m4-p{sid}"},
                "class": {"code": "IMP", "display": "inpatient"},
                "period": {"start": admit, "end": disch},
                "type": [{"text": (row.get("admission_type") or "").title() or None}],
                "reasonCode": [reason] if reason else [],
                "hospitalization": hospitalization,
            }
            yield enc

    def _iter_conditions(self, cohort: list[int]) -> Iterator[dict]:
        sql = f"""
            SELECT d.subject_id, d.hadm_id, d.seq_num, d.icd_code, d.icd_version,
                   ref.long_title,
                   CAST(a.admittime AS STRING) AS admittime
            FROM `{self.dataset_hosp}.diagnoses_icd` d
            LEFT JOIN `{self.dataset_hosp}.d_icd_diagnoses` ref
              ON d.icd_code = ref.icd_code
              AND d.icd_version = ref.icd_version
            LEFT JOIN `{self.dataset_hosp}.admissions` a
              ON d.hadm_id = a.hadm_id
            WHERE d.subject_id IN UNNEST(@subjects)
              AND d.icd_code IS NOT NULL
            ORDER BY d.subject_id, d.hadm_id, d.seq_num
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = int(row["hadm_id"])
            seq = row.get("seq_num")
            code = row["icd_code"]
            version = row.get("icd_version")
            display = row.get("long_title")
            yield {
                "resourceType": "Condition",
                "id": f"m4-dx-{hid}-{seq}",
                "subject": {"reference": f"Patient/m4-p{sid}"},
                "encounter": {"reference": f"Encounter/m4-a{hid}"},
                "code": {
                    "coding": [{
                        "system": _icd_dx_system(version),
                        "code": code,
                        "display": display,
                    }],
                    "text": display,
                },
                "onsetDateTime": _ts(row.get("admittime")),
                "clinicalStatus": {"coding": [{"code": "resolved"}]},
            }

    def _iter_procedures(self, cohort: list[int]) -> Iterator[dict]:
        # MIMIC-IV procedures_icd carries chartdate (a real procedure date),
        # which is more accurate than admittime — use it.
        sql = f"""
            SELECT p.subject_id, p.hadm_id, p.seq_num, p.icd_code, p.icd_version,
                   CAST(p.chartdate AS STRING) AS chartdate,
                   ref.long_title
            FROM `{self.dataset_hosp}.procedures_icd` p
            LEFT JOIN `{self.dataset_hosp}.d_icd_procedures` ref
              ON p.icd_code = ref.icd_code
              AND p.icd_version = ref.icd_version
            WHERE p.subject_id IN UNNEST(@subjects)
              AND p.icd_code IS NOT NULL
            ORDER BY p.subject_id, p.hadm_id, p.seq_num
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = int(row["hadm_id"])
            seq = row.get("seq_num")
            code = row["icd_code"]
            version = row.get("icd_version")
            display = row.get("long_title")
            yield {
                "resourceType": "Procedure",
                "id": f"m4-px-{hid}-{seq}",
                "subject": {"reference": f"Patient/m4-p{sid}"},
                "encounter": {"reference": f"Encounter/m4-a{hid}"},
                "code": {
                    "coding": [{
                        "system": _icd_px_system(version),
                        "code": code,
                        "display": display,
                    }],
                    "text": display,
                },
                "performedDateTime": _ts(row.get("chartdate")),
                "status": "completed",
            }

    def _iter_medications(self, cohort: list[int]) -> Iterator[dict]:
        # MIMIC-IV prescriptions has no row_id. Build a stable id from the
        # natural key (TO_HEX(MD5(...)) yields a 32-char hex string).
        sql = f"""
            SELECT
              TO_HEX(MD5(CONCAT(
                CAST(subject_id AS STRING), '|',
                CAST(IFNULL(hadm_id, 0) AS STRING), '|',
                CAST(IFNULL(pharmacy_id, 0) AS STRING), '|',
                CAST(IFNULL(poe_id, '') AS STRING), '|',
                IFNULL(drug, ''), '|',
                CAST(IFNULL(starttime, DATETIME '1900-01-01') AS STRING)
              ))) AS row_hash,
              subject_id, hadm_id,
              CAST(starttime AS STRING) AS starttime,
              drug, formulary_drug_cd,
              prod_strength, dose_val_rx, dose_unit_rx, route
            FROM `{self.dataset_hosp}.prescriptions`
            WHERE subject_id IN UNNEST(@subjects)
              AND drug IS NOT NULL
            ORDER BY subject_id, hadm_id, starttime, drug
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = row.get("hadm_id")
            rid = row["row_hash"]
            display = row.get("drug")
            if row.get("prod_strength"):
                display = f"{display} {row['prod_strength']}"
            elif row.get("dose_val_rx"):
                display = f"{display} {row['dose_val_rx']} {row.get('dose_unit_rx') or ''}".strip()
            yield {
                "resourceType": "MedicationRequest",
                "id": f"m4-rx-{rid}",
                "subject": {"reference": f"Patient/m4-p{sid}"},
                "encounter": (
                    {"reference": f"Encounter/m4-a{int(hid)}"} if hid is not None else {}
                ),
                "medicationCodeableConcept": {
                    "coding": (
                        [{"code": row["formulary_drug_cd"], "display": display}]
                        if row.get("formulary_drug_cd") else []
                    ),
                    "text": display,
                },
                "authoredOn": (row.get("starttime") or "")[:10] or None,
                "status": "completed",
            }

    def _iter_notes(self, cohort: list[int]) -> Iterator[dict]:
        # Discharge summaries first — these are the clinically definitive
        # documents the agent's _is_key_note filter recognises.
        sql_discharge = f"""
            SELECT note_id, subject_id, hadm_id, note_type,
                   CAST(charttime AS STRING) AS charttime,
                   CAST(storetime AS STRING) AS storetime,
                   text
            FROM `{self.dataset_note}.discharge`
            WHERE subject_id IN UNNEST(@subjects)
              AND hadm_id IS NOT NULL
            ORDER BY subject_id, hadm_id, charttime
        """
        for row in self._query(sql_discharge, {"subjects": cohort}):
            yield from self._build_note(row)

        if self.include_radiology:
            sql_rad = f"""
                SELECT note_id, subject_id, hadm_id, note_type,
                       CAST(charttime AS STRING) AS charttime,
                       CAST(storetime AS STRING) AS storetime,
                       text
                FROM `{self.dataset_note}.radiology`
                WHERE subject_id IN UNNEST(@subjects)
                  AND hadm_id IS NOT NULL
                ORDER BY subject_id, hadm_id, charttime
            """
            for row in self._query(sql_rad, {"subjects": cohort}):
                yield from self._build_note(row)

    def _build_note(self, row: dict) -> Iterator[dict]:
        sid = int(row["subject_id"])
        hid = int(row["hadm_id"])
        note_id = str(row["note_id"])
        # note_id format is e.g. "10000032-DS-21" — already URL-safe but we
        # sanitize defensively in case PhysioNet adds new separators.
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", note_id)
        note_type = (row.get("note_type") or "").strip()
        category = _NOTE_TYPE_MAP.get(note_type, note_type or None)
        # MIMIC-IV doesn't ship a per-note description; carry note_type as
        # description for traceability in the notes_index sidebar.
        description = note_type or None
        # charttime is the clinical event time; storetime is when it landed
        # in the EHR. chartdate isn't a column in MIMIC-IV — derive from
        # charttime so downstream filters (which prefer chartdate) work.
        charttime = row.get("charttime")
        chartdate = (charttime or "")[:10] or None
        yield {
            "resourceType": "Note",
            "id": f"m4-note-{safe_id}",
            "subject": {"reference": f"Patient/m4-p{sid}"},
            "encounter": {"reference": f"Encounter/m4-a{hid}"},
            "category": category,
            "description": description,
            "chartdate": chartdate,
            "charttime": charttime,
            "text": row.get("text"),
        }

    def _iter_labs(self, cohort: list[int]) -> Iterator[dict]:
        # Filter to admission-scoped labs (hadm_id IS NOT NULL) so the
        # ingest's encounter FK isn't violated. Outpatient labs (no hadm_id)
        # are dropped — the agent's clinical context is admission-centric.
        # MIMIC-IV's d_labitems doesn't carry loinc_code, so observations have
        # text-only codes (no LOINC system URI).
        sql = f"""
            SELECT le.labevent_id, le.subject_id, le.hadm_id, le.itemid,
                   CAST(le.charttime AS STRING) AS charttime,
                   le.value, le.valuenum, le.valueuom, le.flag,
                   d.label, d.fluid, d.category
            FROM `{self.dataset_hosp}.labevents` le
            LEFT JOIN `{self.dataset_hosp}.d_labitems` d USING (itemid)
            WHERE le.subject_id IN UNNEST(@subjects)
              AND le.hadm_id IS NOT NULL
            ORDER BY le.subject_id, le.hadm_id, le.charttime
        """
        for row in self._query(sql, {"subjects": cohort}):
            sid = int(row["subject_id"])
            hid = int(row["hadm_id"])
            lid = int(row["labevent_id"])
            label = row.get("label") or f"itemid {row['itemid']}"
            obs: dict = {
                "resourceType": "Observation",
                "id": f"m4-lab-{lid}",
                "subject": {"reference": f"Patient/m4-p{sid}"},
                "encounter": {"reference": f"Encounter/m4-a{hid}"},
                "code": {"coding": [], "text": label},
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
    include_notes: bool = True,
    include_radiology: bool = False,
    project_id: str | None = None,
) -> MimicIvBigQuerySource:
    """Build a configured MimicIvBigQuerySource using env vars as defaults."""
    pid = project_id or os.environ.get("BQ_PROJECT_ID")
    if not pid:
        raise RuntimeError(
            "BQ_PROJECT_ID is not set. Pass --bq-project or export BQ_PROJECT_ID="
            "<your-gcp-project>."
        )
    return MimicIvBigQuerySource(
        project_id=pid,
        cohort_size=cohort_size if cohort_size is not None else int(
            os.environ.get("MIMIC_COHORT_SIZE", "100")
        ),
        cohort_strategy=cohort_strategy or os.environ.get(
            "MIMIC_COHORT_STRATEGY", "readmit"
        ),
        include_labs=include_labs,
        include_notes=include_notes,
        include_radiology=include_radiology,
    )
