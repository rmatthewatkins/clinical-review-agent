# Clinical Review Agent

An AI-powered clinical peer-review agent that ingests patient data from synthetic sources (Synthea), production FHIR R4 servers (Epic, HAPI), or MIMIC-III / MIMIC-IV on BigQuery, then identifies and reviews specific case types — currently 30-day hospital readmissions, inpatient mortality, and AHRQ Patient Safety Indicators (PSIs). Uses Claude as a simulated hospitalist to perform structured peer reviews with root cause analysis, preventability scoring, and intervention recommendations. PSI review is specifically tuned to identify documentation and coding gaps — findings present in radiology or discharge narrative that were never coded into structured ICD diagnoses, which routinely cause AHRQ indicators to misclassify cases.

## Architecture

```
clinical_review_agent/           Python package
├── schema.py                    PostgreSQL schema (10 tables) — the contract between modules
├── config.py                    Centralized env-driven settings
├── logging_config.py            Structured JSON / text logging
├── data/
│   ├── ingest.py                Source-agnostic resource parser → PostgreSQL
│   ├── pairs.py                 30-day readmission identifier (planned-readmit + transfer filters)
│   ├── mortality.py             Inpatient mortality identifier (death-disposition match)
│   ├── psi.py                   AHRQ PSI engine (numerator/denominator/exclusion + POA inference)
│   ├── psi_definitions/         Per-PSI rule subclasses (PSI 03, 06, 12 — prototype subset)
│   │   ├── base.py              PsiDefinition base class
│   │   ├── psi_03.py            Pressure Ulcer Rate (stage 3+, POA-critical)
│   │   ├── psi_06.py            Iatrogenic Pneumothorax Rate
│   │   └── psi_12.py            Perioperative Pulmonary Embolism / DVT Rate
│   └── sources/
│       ├── base.py              FhirSource protocol + shared FHIR parsing helpers
│       ├── synthea_file.py      File-based source (Synthea Bundle JSONs)
│       ├── fhir_api.py          FHIR R4 REST API client (Epic, HAPI, etc.)
│       ├── mimic_bq.py          MIMIC-III via BigQuery (physionet-data) → FHIR + Note dicts
│       └── mimic_iv_bq.py       MIMIC-IV via BigQuery (physionet-data.mimiciv_*) → FHIR + Note dicts
├── agent/
│   ├── context.py               Clinical context assembler (per case type)
│   └── reviewer.py              Anthropic API call + structured peer review
└── analytics/
    └── analyze.py               Cohort metrics + matplotlib visualizations

main.py                          Typer CLI entry point
server.py                        FastAPI REST API (port 8000)
tests/                           Pytest suite
web/                             Next.js + TypeScript + Tailwind frontend (port 3000)
├── app/
│   ├── page.tsx                 Dashboard
│   ├── readmissions/            Readmission case list
│   ├── mortality/               Mortality case list
│   ├── reviews/                 Reviews list + detail (with "Run AI Review" trigger)
│   └── analytics/               Cohort analytics
└── lib/api.ts                   API client + TypeScript types
```

### Pipeline

```
Source (Synthea | FHIR R4 API | MIMIC-III BigQuery | MIMIC-IV BigQuery)
   ↓ ingest      (parse + load to PostgreSQL)
   ↓ identify    (readmission pairs OR mortality cases OR PSI candidates)
   ↓ review      (Claude assembles context, scores, narrates, surfaces coding gaps)
   ↓ analyze     (cohort metrics + charts)
```

The `FhirSource` protocol (`iter_resources() -> Iterator[dict]`) lets every downstream stage stay source-agnostic. Synthea and the FHIR API client emit FHIR R4 resource dicts directly. The MIMIC-III source emits FHIR-shaped dicts plus a custom `Note` resourceType for free-text narrative — FHIR R4 doesn't carry note text (that travels via C-CDA in real EHR integrations); MIMIC hands it to us via `mimiciii_notes.noteevents`.

## Prerequisites

- Python 3.11+
- Node.js 18+ (for the web UI)
- A PostgreSQL instance (Railway hosted, or local — `DATABASE_URL` env var)
- An Anthropic API key (`ANTHROPIC_API_KEY` env var)
- For Synthea source: Java (to run Synthea)
- For MIMIC-III / MIMIC-IV sources: a GCP project with BigQuery enabled, `gcloud auth application-default login` set up, and PhysioNet credentialed access to the dataset(s) you want to query (granted per-version, not shared)

## Setup

```bash
git clone https://github.com/rmatthewatkins/clinical-review-agent.git
cd clinical-review-agent

# Python — editable install with all extras
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mimic]"

# Frontend
cd web && npm install && cd ..

# Required env vars
cp .env.example .env
# then edit .env to set ANTHROPIC_API_KEY and DATABASE_URL
```

`pip install -e ".[mimic]"` adds `google-cloud-bigquery`. Skip this extra if you only need the Synthea/FHIR-API sources. `pip install -e ".[dev]"` adds the test runner.

## Data sources

### A. Synthea (synthetic FHIR)

```bash
# Build Synthea
git clone https://github.com/synthetichealth/synthea.git /tmp/synthea
cd /tmp/synthea && ./gradlew build check -x test

# Generate older patients — they have more inpatient encounters
./run_synthea -p 500 -a 50-90

# Output: /tmp/synthea/output/fhir/*.json
```

Younger Synthea cohorts produce too few inpatient encounters to drive readmission analysis. 500 patients aged 50–90 is the recommended starting set.

### B. FHIR R4 server (Epic, HAPI, etc.)

Any FHIR R4 endpoint with `Patient`, `Encounter`, `Condition`, `MedicationRequest`, `Observation`, and `Procedure` support. Bearer-token auth via `--token` or `FHIR_TOKEN` env var.

### C. MIMIC-III via BigQuery

Reads directly from `physionet-data.mimiciii_clinical` and `physionet-data.mimiciii_notes` (PhysioNet credentialed). The source translates MIMIC tables into FHIR R4 dicts plus custom `Note` resources. Resource ids are namespaced `m3-p<subject_id>`, `m3-a<hadm_id>`, `m3-note-<row_id>`, etc., so they survive a roundtrip and can deep-link into a separate chart-browser if you have one.

Requires `BQ_PROJECT_ID` env var pointing at a GCP project that can be billed for queries (the dataset itself is public; the project handles billing). Note: if your global ADC has a `quota_project_id` set to a different project the active account can't bill, you'll get `USER_PROJECT_DENIED`. The source overrides this programmatically — see `clinical_review_agent/data/sources/mimic_bq.py`.

### D. MIMIC-IV via BigQuery

Reads from `physionet-data.mimiciv_3_1_hosp` (versioned by AHRQ release) and `physionet-data.mimiciv_note` for clinical narrative. The source mirrors MIMIC-III's behavior with these adaptations: synthesized `birthDate` from `anchor_year - anchor_age` (MIMIC-IV publishes no DOB), per-row `icd_version` (9 or 10) selecting the right FHIR coding system, MD5-hashed prescription ids (no `row_id` in MIMIC-IV `prescriptions`), and `discharge.note_type = "DS"` mapped to category `"Discharge summary"` so the existing key-note filter picks it up. Add `--include-radiology` to also pull `mimiciv_note.radiology` reports — required if you plan to run the PSI agent (which reads radiology to spot uncoded findings).

```bash
python main.py ingest --mimic-iv-bq --bq-project mimic-iii-exploration \
  --cohort-size 25 --include-radiology
```

Use `--mimic-iv-version 3_1` (default) or `--mimic-iv-version 2_2` to pin a different release. Resource ids are namespaced `m4-p<subject_id>`, `m4-a<hadm_id>`, `m4-note-<note_id>`, etc., so MIMIC-III and MIMIC-IV can coexist in one Postgres without collision (though the DUA still recommends separate databases).

**DUA notice:** MIMIC-III and MIMIC-IV are both restricted under the PhysioNet Credentialed Health Data Use Agreement v1.5. Single credentialed user only; no public deployment of MIMIC-loaded systems; outputs (`output/reviews/*`) are also restricted data. Use a dedicated Postgres database for MIMIC ingest — don't co-mingle with Synthea data. PhysioNet grants BigQuery access per dataset; having MIMIC-III access does not imply MIMIC-IV access (and the unversioned aliases like `mimiciv_hosp` may require a separate grant from the versioned `mimiciv_3_1_hosp`).

## Usage

```bash
source .venv/bin/activate
set -a && source .env && set +a   # or .env.mimic for the MIMIC database
```

### 1. Ingest

```bash
# Synthea files
python main.py ingest --dir /tmp/synthea/output/fhir

# FHIR R4 server
python main.py ingest --fhir-url http://localhost:8080/fhir
python main.py ingest --fhir-url https://fhir.example.com/R4 --token "$FHIR_TOKEN"

# MIMIC-III BigQuery
python main.py ingest --mimic-bq --bq-project mimic-iii-exploration \
  --cohort-size 100 --cohort-strategy readmit \
  --include-labs --include-notes

# Selective backfill (only re-fetch one resource type for an existing cohort)
python main.py ingest --mimic-bq --cohort-size 100 --only Note
```

`--cohort-strategy` choices for the MIMIC source: `readmit` (patients with ≥1 candidate 30-day readmit), `high-acuity` (patients with the most admissions), `random` (deterministic shuffle). Default `readmit`.

`--only` overrides the include flags and emits only the listed resource type(s) — useful when you've already ingested the heavy tables and want to backfill notes (or any single resource) without re-scanning everything.

`--dry-run` on `ingest` iterates the source and prints resourceType counts + a sample dict per type without writing to PostgreSQL — great for verifying BQ access and translation shape before committing to a full ingest.

### 2. Identify cases

```bash
python main.py identify                       # readmissions (default)
python main.py identify --type mortality      # inpatient mortality
python main.py identify --type psi            # AHRQ Patient Safety Indicators
```

**Readmissions:** finds inpatient encounters followed by another inpatient encounter for the same patient within `READMISSION_WINDOW_DAYS` (default 30). Filters out:
- Transfers (gap ≤ `READMISSION_MIN_DAYS`, default 2)
- Encounters discharged to SNF / rehab / hospice / LTAC / cancer-transfer
- Planned surgical follow-ups (description matches `history of`, `aftercare`, etc.)
- Same-`reason_code` pairs (recurring chemo, dialysis, etc.)

**Mortality:** finds inpatient encounters with discharge dispositions matching `expired`, `died`, `death`, `deceased`. Each case stores a configurable lookback window (`MORTALITY_LOOKBACK_DAYS`, default 90 days) used by the context builder to summarize prior care.

**PSI:** runs each registered AHRQ Patient Safety Indicator over every inpatient encounter. A row is written to `psi_cases` for each encounter where the PSI's numerator fires, regardless of whether the denominator/exclusion logic kept the case — exclusion reasons are recorded on the row so the review agent can decide whether the engine got it right (e.g. an excluded case might still be a true PSI hit if the exclusion code was an artifact, and a numerator hit might not be a real event if a *missing* exclusion would have removed it). Currently registered: PSI 03 (Pressure Ulcer), PSI 06 (Iatrogenic Pneumothorax), PSI 12 (Perioperative VTE) — each per AHRQ QI v2024 (curated subsets, not exhaustive code lists). Adding a new PSI is one new file in `psi_definitions/` plus one line in `REGISTRY`.

**POA strategy:** MIMIC-IV does not carry present-on-admission flags. Each PSI infers POA via prior-admission lookback — if the same code appeared on a strictly earlier admission for the patient, the condition is treated as POA-likely. The engine records `poa_imputation` (`prior_admission_lookback` / `assumed_not_poa`) and `poa_confidence` (`high` / `medium` / `low`) on each `psi_cases` row. The review agent treats this as a hypothesis and overrides it from the discharge / radiology narrative when there's better evidence.

All three commands are idempotent — they DELETE existing rows of their case type and re-identify.

### 3. Review

```bash
python main.py review --dry-run                   # preview context, no API call
python main.py review --limit 10                  # review next 10 unreviewed readmissions
python main.py review                             # review all unreviewed readmissions

python main.py review --type mortality --dry-run  # preview a mortality context
python main.py review --type mortality --limit 5  # review 5 mortality cases

python main.py review --type psi --dry-run        # preview a PSI context
python main.py review --type psi --limit 5        # review 5 PSI candidates
```

Each review produces:
- **Structured JSON** — `root_cause_category`, `preventability_score` (1-5), `preventability_rationale`, `contributing_factors`, `recommended_interventions`, `confidence_level`. Stored in `reviews.structured_json`.
- **Clinical narrative** — 2-3 paragraph M&M-style prose. Stored in `reviews.clinical_narrative`.

Outputs land in PostgreSQL (`reviews` table, upsert keyed on `(case_type, case_id)`) and as `output/reviews/{case_type}_{case_id}.{json,md}` files. The MD files are restricted data when produced from MIMIC ingest — handle accordingly.

The clinical context payload includes:
- `patient_baseline` (demographics, chronic conditions, baseline meds, recent observations)
- The case-type-specific encounter:
  - **Readmission** — `index_admission` + `interval_care` + `readmission`
  - **Mortality** — `death_encounter` + `prior_care`
  - **PSI** — `psi` (engine verdict + POA imputation) + `encounter` + `prior_encounters`
- For each encounter, **`key_notes`** (full text of discharge summaries + admission H&P notes; PSI review additionally pulls every radiology report) and **`notes_index`** (metadata-only list of all other notes for that encounter — category, description, date)

The system prompt explicitly instructs Claude to read `key_notes` first and to use `notes_index` to flag missing documentation rather than invent narrative. The PSI prompt adds explicit instructions to compare coded diagnoses against radiology / discharge narrative and emit `missing_codes[]` (with suggested ICD-10, evidence quote, source-note citation) for findings that should have been coded — the canonical "documentation/coding gap" finding that flips a PSI false-positive into a real CDI opportunity.

### 4. Analyze

```bash
python main.py analyze                       # all case types
python main.py analyze --type readmission    # readmissions only
python main.py analyze --type mortality      # mortality only
```

Produces `output/analytics/`:
- `root_cause_distribution.png` — root-cause category counts
- `preventability_by_diagnosis.png` — mean preventability per diagnosis
- `contributing_factors.png` / `recommended_interventions.png` — top phrases
- `preventability_histogram.png` — score distribution (1-5)
- `summary.json` — all computed metrics

### 5. Web UI

```bash
# Terminal 1
set -a && source .env && set +a
python server.py        # FastAPI on http://localhost:8000

# Terminal 2
cd web && npm run dev   # Next.js on http://localhost:3000
```

Pages:
- **Dashboard** — patient/encounter/case/review counts, root cause distribution, preventability charts, quick links
- **Readmissions** — readmission pair list with patient demographics, encounter details, review status
- **Mortality** — mortality case list with death disposition + lookback details
- **Reviews** — completed reviews with preventability badges, root-cause pills, confidence indicators; filterable by `case_type`
- **Review Detail** — two-column view: clinical context (left) + AI assessment + narrative (right). "Run AI Review" button triggers a Claude review on Pending cases (rate-limited).
- **Analytics** — multi-dimension drill-down across root cause, diagnosis, gender, age group, days to readmission, factors, interventions

## Database schema

11 tables (`clinical_review_agent/schema.py`):

| Table | Purpose |
|---|---|
| `patients` | demographics, location, race/ethnicity |
| `encounters` | type (`inpatient` / `ambulatory` / `emergency`), period, reason, discharge disposition |
| `conditions` | diagnoses with onset/abatement and clinical status |
| `medications` | prescriptions / medication requests with status and dates |
| `observations` | lab values, vitals, screening scores |
| `procedures` | performed procedures with dates |
| `care_plans` | active care plans with status |
| `notes` | free-text narrative (category, description, chartdate, charttime, full text) — MIMIC-only at present |
| `readmissions` | identified index → readmission encounter pairs with `days_between` |
| `mortality_cases` | identified inpatient deaths with `death_date` and `lookback_days` |
| `psi_cases` | identified AHRQ PSI candidates: `psi_number`, numerator/denominator verdicts, exclusion reasons, POA imputation method + confidence |
| `reviews` | AI assessments, polymorphic via `(case_type, case_id)` unique index — supports `readmission`, `mortality`, `psi` |

`SCHEMA_SQL` is idempotent (`CREATE TABLE IF NOT EXISTS`); `MIGRATION_SQL` upgrades older deployments (renames `readmission_pairs → readmissions`, adds `case_type`/`case_id` to `reviews`, etc.) and is safe on fresh databases.

## Review schemas

### Readmission

| Field | Values |
|---|---|
| `root_cause_category` | `premature_discharge`, `inadequate_transition_planning`, `medication_related`, `inadequate_follow_up`, `disease_progression`, `social_determinants`, `patient_behavioral`, `unavoidable`, `other` |
| `preventability_score` | 1 (clearly not preventable) – 5 (clearly preventable) |
| `confidence_level` | `high`, `moderate`, `low` |

### Mortality

| Field | Values |
|---|---|
| `root_cause_category` | `diagnostic_error`, `treatment_delay`, `medication_error`, `system_failure`, `disease_progression`, `comorbidity_burden`, `communication_failure`, `unavoidable`, `other` |
| `preventability_score` | 1–5 (same anchors) |
| `confidence_level` | `high`, `moderate`, `low` |

Readmission and mortality share the prose-narrative output and the `contributing_factors` / `recommended_interventions` arrays.

### PSI (Patient Safety Indicator)

| Field | Values |
|---|---|
| `root_cause_category` | `coding_gap`, `documentation_gap`, `poa_misclassification`, `true_event_preventable`, `true_event_unavoidable`, `engine_error`, `other` |
| `case_classification` | `true_positive`, `false_positive_coding`, `false_positive_documentation`, `false_positive_poa`, `false_positive_logic`, `insufficient_data` |
| `missing_codes[]` | Each: `code` (suggested ICD-10), `display`, `evidence` (quote), `source_note` |
| `documentation_opportunities[]` | Findings only present in non-codeable sources — CDI engagement candidates |
| `poa_assessment` | `agent_inference` (`poa` / `not_poa` / `unable_to_determine`), `confidence`, `rationale` |
| `preventability_score` | 1–5 (only meaningful for `true_event_*`; `1` for false positives) |
| `recommended_actions[]` | Concrete next steps for coding / CDI / quality / engine teams |
| `confidence_level` | `high`, `moderate`, `low` |

## Testing

```bash
python -m pytest tests/ -v
python -m pytest tests/test_data.py -v          # ingest + readmission identifier
python -m pytest tests/test_mortality.py -v     # mortality identifier + context
python -m pytest tests/test_agent.py -v         # context assembly + response parsing
python -m pytest tests/test_sources.py -v       # source adapters (Synthea + Epic compat)
python -m pytest tests/test_analytics.py -v     # analytics + chart generation
python -m pytest tests/test_server.py -v        # FastAPI endpoints
```

Tests default to `postgresql://localhost/readmissions_test` (override with `TEST_DATABASE_URL`). They never touch the production Railway DB — `conftest.py` enforces this.

## Configuration

All settings load from environment variables via `clinical_review_agent/config.py:get_settings()`. Defaults are sensible for local development.

| Env var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | (required) | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | (required for review) | Claude API key |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Anthropic model |
| `CLAUDE_MAX_TOKENS` | `4096` | Output cap per review |
| `CLAUDE_MAX_RETRIES` | `5` | Retry count on 429 |
| `READMISSION_WINDOW_DAYS` | `30` | Max gap between discharge and readmit |
| `READMISSION_MIN_DAYS` | `2` | Below this is treated as a transfer |
| `READMISSION_EXCLUDE_TRANSFER_DISPOSITIONS` | `true` | Skip SNF/Rehab/Hospice/LTAC/etc. |
| `READMISSION_EXCLUDE_SURGICAL_FOLLOWUP` | `true` | Skip "history of"/"aftercare"/etc. |
| `MORTALITY_LOOKBACK_DAYS` | `90` | Window for mortality `prior_care` |
| `BQ_PROJECT_ID` | (required for `--mimic-bq`) | GCP billing project for BigQuery |
| `MIMIC_COHORT_SIZE` | `100` | Default cohort size for MIMIC source |
| `MIMIC_COHORT_STRATEGY` | `readmit` | `readmit` / `high-acuity` / `random` |
| `FHIR_TOKEN` | — | Bearer token for `--fhir-url` |
| `FHIR_PAGE_SIZE` | `100` | FHIR API `_count` |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowlist |
| `SERVER_HOST` / `SERVER_PORT` | `0.0.0.0` / `8000` | FastAPI bind |
| `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | `2` / `10` | psycopg pool |
| `REVIEW_RATE_LIMIT` | `10` | Reviews per minute via API |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | Logging |

See `.env.example` for the full list.

## Docker

```bash
docker compose up           # db + api + web
docker compose up db        # just the database
```

The included `docker-compose.yml` provisions a local Postgres (port 5432), the FastAPI backend, and the Next.js frontend. Use this for self-contained local development; for production / personal Railway-backed runs, use the venv flow above.
