# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Commands

```bash
# Setup — Python backend (editable install with dev + MIMIC extras)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mimic]"

# Setup — Next.js frontend
cd web && npm install && cd ..

# Run tests (default DB: postgresql://localhost/readmissions_test — never Railway)
python -m pytest tests/ -v
python -m pytest tests/test_data.py -v               # ingest + readmission identifier
python -m pytest tests/test_sources.py -v            # source adapters (Synthea + FHIR API + MIMIC)
python -m pytest tests/test_agent.py::TestResponseParsing -v
python -m pytest tests/test_analytics.py -k "test_counts" -v
python -m pytest tests/test_server.py -v             # API endpoint tests
python -m pytest tests/test_mortality.py -v          # mortality identification + context

# CLI — source the right .env first (DATABASE_URL + ANTHROPIC_API_KEY at minimum)
set -a && source .env && set +a

# Ingest from each source
python main.py ingest --dir <synthea-fhir-dir>
python main.py ingest --fhir-url http://localhost:8080/fhir
python main.py ingest --mimic-bq --bq-project <gcp-project> --cohort-size 100
python main.py ingest --mimic-bq --cohort-size 100 --only Note    # selective backfill
python main.py ingest --mimic-iv-bq --bq-project <gcp-project> --cohort-size 100
python main.py ingest --mimic-iv-bq --cohort-size 100 --include-radiology    # add radiology reports

python main.py identify                              # readmissions (default)
python main.py identify --type mortality

python main.py review --dry-run                      # preview context only
python main.py review --limit N
python main.py review --type mortality --dry-run
python main.py review --type mortality --limit N

python main.py analyze
python main.py analyze --type readmission
python main.py analyze --type mortality

# Web app — two terminals
set -a && source .env && set +a && python server.py  # FastAPI on :8000
cd web && npm run dev                                  # Next.js on :3000

# Docker (alternative — provisions local Postgres + API + web)
docker compose up
docker compose up db                                   # just the database

# Synthea (requires Java)
cd /tmp/synthea && ./run_synthea -p 500 -a 50-90
```

## Architecture

PostgreSQL (Railway-hosted in production, local in tests) is the shared contract between modules. `clinical_review_agent/schema.py` defines 10 tables and `get_connection()` returns an initialized psycopg connection with the `dict_row` factory. All modules import from `clinical_review_agent.*`. `DATABASE_URL` is required — `RuntimeError` if missing.

**Pipeline:** `source adapter → ingest (parse + load) → identify (readmission | mortality) → review → analyze`

The package layout uses a flat-package convention (`clinical_review_agent/` at the project root, not `src/clinical_review_agent/`). Editable install via `pip install -e .` is wired through `[tool.hatch.build.targets.wheel] packages = ["clinical_review_agent"]` in `pyproject.toml`.

### Configuration (`clinical_review_agent/config.py`)

Centralized settings via `get_settings()`. All knobs (model name, retry counts, CORS origins, pool sizes, readmission window, mortality lookback, MIMIC cohort defaults, rate limits, logging format) live here. See `.env.example` for the exhaustive list. `DATABASE_URL` is the only required value.

### Web UI

- **`server.py`** — FastAPI backend (port 8000). Uses `psycopg_pool.ConnectionPool`. Health/readiness endpoints, request-logging middleware, global error handler, rate-limited review trigger, paginated list endpoints.
- **`web/`** — Next.js 16 + TypeScript + Tailwind frontend (port 3000). App Router with 5 pages: Dashboard, Readmissions, Mortality, Reviews, Analytics.
- **Health endpoints:** `GET /health` (liveness), `GET /ready` (readiness — checks DB).
- **API endpoints:** `GET /api/summary`, `GET /api/readmissions`, `GET /api/readmissions/{id}`, `GET /api/mortality-cases`, `GET /api/mortality-cases/{id}`, `GET /api/reviews` (optional `?case_type=`), `GET /api/reviews/{id}` (legacy readmission), `GET /api/reviews/{case_type}/{case_id}` (generalized), `POST /api/reviews/{case_type}/{case_id}/run` (triggers AI review, rate-limited), `POST /api/reviews/{id}/run` (legacy readmission trigger), `GET /api/analytics` (optional `?case_type=`), `GET /api/analytics/by-root-cause`, `GET /api/analytics/by-diagnosis`, `GET /api/analytics/by-gender`, `GET /api/analytics/by-age-group`, `GET /api/analytics/by-days-between`.
- **Pagination:** `GET /api/readmissions` and `GET /api/reviews` accept `?offset=N&limit=N` (default `limit=50`, max `200`). Responses are `{total, offset, limit, items}`.

### Logging (`clinical_review_agent/logging_config.py`)

Structured JSON by default (`LOG_FORMAT=json`); human-readable text for local dev (`LOG_FORMAT=text`). Request-logging middleware logs method, path, status, duration. `LOG_LEVEL` and `LOG_FORMAT` env vars control behavior.

### Data source layer (`clinical_review_agent/data/sources/`)

The `FhirSource` protocol (`base.py`) defines a single method: `iter_resources() -> Iterator[dict]`. **Four** implementations:

- **`SyntheaFileSource`** (`synthea_file.py`) — Reads FHIR Bundle JSON files from disk. Used for dev/test with Synthea.
- **`FhirApiSource`** (`fhir_api.py`) — FHIR R4 REST client (Epic, HAPI, etc.). Pagination, bearer-token auth via callable `token_provider`, exponential backoff. Designed for production Epic.
- **`MimicBigQuerySource`** (`mimic_bq.py`) — Queries `physionet-data.mimiciii_clinical` and `physionet-data.mimiciii_notes` and translates rows into FHIR R4 resource dicts (Patient, Encounter, Condition, Procedure, MedicationRequest, Observation) **plus** a custom `Note` resourceType. Resource ids are namespaced `m3-*` (e.g. `m3-p<subject_id>`, `m3-a<hadm_id>`, `m3-note-<row_id>`). Programmatically overrides ADC `quota_project_id` to avoid `USER_PROJECT_DENIED` when the global gcloud install points at a different billing project. Cohort selection strategies: `readmit` (default — patients with ≥1 candidate 30-day readmission), `high-acuity` (most admissions), `random` (deterministic shuffle). `--only <Type>` lets ingest emit a single resource type for selective backfill.
- **`MimicIvBigQuerySource`** (`mimic_iv_bq.py`) — Same protocol/strategies as the MIMIC-III source, but queries `physionet-data.mimiciv_hosp` and `physionet-data.mimiciv_note`. Ids are namespaced `m4-*` so they cannot collide with `m3-*` if a database is mixed. Key MIMIC-IV adaptations: (1) `Patient.birthDate` is synthesized as `{anchor_year - anchor_age}-01-01` (MIMIC-IV ships no DOB, only an anchor year/age pair); (2) demographics use `race` (per-admission, latest) instead of `ethnicity`; (3) ICD diagnoses/procedures carry an `icd_version` (9 or 10) that selects the FHIR coding system; (4) `d_labitems` no longer has `loinc_code`, so lab Observations have `code.text` only; (5) prescriptions get a stable `m4-rx-<md5(natural-key)>` id since the table has no `row_id`; (6) notes come from the `discharge` table by default (mapped to category "Discharge summary" so the agent's key-note filter picks them up); pass `--include-radiology` to also pull the `radiology` table. Physician/H&P notes aren't published in MIMIC-IV.

Shared FHIR parsing helpers live in `base.py`: `ref()` (strips reference prefixes — handles `urn:uuid:`, `Patient/id`, full URLs), `code_display()` (extracts from CodeableConcept), `extension_value()` (reads US Core extensions).

### Ingest + parsers (`clinical_review_agent/data/ingest.py`)

`run_ingest(source)` accepts any `FhirSource`, iterates resources, routes each through `_parse_*` functions, and batch-inserts into PostgreSQL. Uses `SET CONSTRAINTS ALL DEFERRED` during bulk load and `ON CONFLICT DO NOTHING` for idempotency. The parsers handle Synthea, Epic, and MIMIC patterns:

- `_parse_medication`: falls back from `medicationCodeableConcept` (Synthea) to `medicationReference.display` (Epic).
- `_parse_encounter`: falls back from `reasonCode` (Synthea) to `reasonReference` (Epic).
- `_parse_observation`: handles `component` arrays (e.g., blood pressure systolic/diastolic).
- `_parse_note`: handles the custom `Note` resourceType (id, subject, encounter, category, description, chartdate, charttime, text). Note is non-FHIR by design — FHIR R4 doesn't carry note narrative; that travels via C-CDA in real EHR integrations.
- Backwards compatible: `run_ingest("path/to/dir")` still works (auto-wraps in `SyntheaFileSource`).

Registered resource types in `_PARSERS`: `Patient`, `Encounter`, `Condition`, `MedicationRequest`, `Observation`, `Procedure`, `CarePlan`, `Note`.

### Downstream (source-agnostic)

- **`clinical_review_agent/data/pairs.py`** — Identifies 30-day readmissions from inpatient encounters. Configurable readmission window (`READMISSION_WINDOW_DAYS`, default 30). Filters: minimum-day gap (transfer exclusion), discharge-disposition pattern match, planned-surgical-followup description match, same-`reason_code` planned readmissions. Idempotent (clears `readmissions` and `WHERE case_type='readmission'` reviews before re-identifying).

- **`clinical_review_agent/data/mortality.py`** — Identifies inpatient mortality cases from discharge dispositions matching `expired`/`died`/`death`/`deceased`. Inserts into `mortality_cases` with a `lookback_days` value (`MORTALITY_LOOKBACK_DAYS`, default 90). Idempotent: clears existing mortality cases + their reviews before re-identifying.

- **`clinical_review_agent/agent/context.py`** — Assembles clinical context dicts. Dispatches via `assemble_context(case_type, row, conn)` to type-specific builders. Readmission context: `patient_baseline`, `index_admission`, `interval_care`, `readmission`. Mortality context: `patient_baseline`, `death_encounter`, `prior_care`. Each encounter section includes `key_notes` (full text of discharge summaries + admission H&P-style physician notes) and `notes_index` (metadata-only list of all notes attached to that encounter — category, description, chartdate, charttime). Backwards-compatible: `assemble_context(row, conn)` still works for readmissions. `context_to_prompt_string()` serializes to JSON for the LLM.

- **`clinical_review_agent/agent/reviewer.py`** — Sends the serialized context to Claude with a case-type-specific system prompt (`SYSTEM_PROMPTS` dict). Each prompt has a "How the Data is Structured" section telling the model to read `key_notes` first and use `notes_index` to flag missing documentation rather than invent narrative. Readmission and mortality have different clinical frameworks and `root_cause_category` enums:
  - **Readmission categories:** `premature_discharge`, `inadequate_transition_planning`, `medication_related`, `inadequate_follow_up`, `disease_progression`, `social_determinants`, `patient_behavioral`, `unavoidable`, `other`.
  - **Mortality categories:** `diagnostic_error`, `treatment_delay`, `medication_error`, `system_failure`, `disease_progression`, `comorbidity_burden`, `communication_failure`, `unavoidable`, `other`.
  Extracts structured JSON from ` ```json``` ` fences and narrative from the remainder. Stores in `reviews` table with `(case_type, case_id)` upsert + writes `output/reviews/{case_type}_{case_id}.{json,md}`. Exponential backoff on 429s. Model and retry params come from `clinical_review_agent/config.py`.

- **`clinical_review_agent/analytics/analyze.py`** — Cohort metrics + matplotlib PNGs to `output/analytics/` + `summary.json`. Must call `matplotlib.use('Agg')` before any other matplotlib import (headless rendering).

**CLI wiring:** `main.py` uses Typer with lazy imports. `ingest` accepts exactly one of `--dir` (file source), `--fhir-url` (API source), `--mimic-bq` (MIMIC-III), or `--mimic-iv-bq` (MIMIC-IV). The MIMIC flags share `--bq-project`, `--cohort-size`, `--cohort-strategy`, `--include-labs/--no-include-labs`, `--include-notes/--no-include-notes`, `--only <Type>`, `--dry-run`. `--include-radiology/--no-include-radiology` is MIMIC-IV-only. `identify`, `review`, `analyze` accept `--type readmission|mortality`.

## Key patterns

- **Database:** PostgreSQL on Railway via `psycopg` (v3) with `dict_row` factory. Connection URL from `DATABASE_URL` (required — raises `RuntimeError` if missing). Schema uses `DEFERRABLE` foreign keys for bulk-load support. The `"end"` column is quoted in SQL (reserved word). `MIGRATION_SQL` runs before `SCHEMA_SQL` and handles legacy renames (`readmission_pairs → readmissions`, `pair_id → readmission_id`); guarded by table-existence checks so it's a no-op on fresh databases. The `reviews` table is generalized via `case_type` + `case_id` (unique index) instead of per-type FK columns. The `mortality_cases` table holds identified deaths with their lookback window. The `notes` table stores free-text narrative (MIMIC-only at present).
- **Connection pooling:** `server.py` uses `psycopg_pool.ConnectionPool` (min 2, max 10, configurable via `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE`). Pool initialized in the FastAPI lifespan handler.
- **DB in tests:** Tests default to `postgresql://localhost/readmissions_test` (NOT Railway). `conftest.py` patches `DATABASE_URL` and `config.settings` and provides a `db_conn` fixture that truncates all tables between tests. Override with `TEST_DATABASE_URL`.
- **SQL placeholders:** All queries use `%s` (psycopg format).
- **API key:** Read from `ANTHROPIC_API_KEY` env var (stored in `.env`, not auto-loaded — must `source` before running).
- **FHIR token:** `FHIR_TOKEN` env var or `--token` flag.
- **MIMIC source auth:** `BQ_PROJECT_ID` env var picks the GCP billing project. The source builds its own `GoogleAuth` instance and overrides `quotaProjectId` on the credentials before constructing the BigQuery client — bypasses any stale `quota_project_id` in `~/.config/gcloud/application_default_credentials.json`.
- **Model:** `CLAUDE_MODEL` env var (default `claude-sonnet-4-6`), wired through `clinical_review_agent/config.py`.
- **`reviews.structured_json`** stores a JSON string parsed via `json.loads()`. Fields: `root_cause_category`, `preventability_score` (1-5), `preventability_rationale`, `contributing_factors[]`, `recommended_interventions[]`, `confidence_level`. The `root_cause_category` enum differs per case type.
- **Multi-case-type architecture:** Adding a new case type requires (1) a case table in schema, (2) an identification module in `data/`, (3) a context builder branch in `agent/context.py`, (4) a system prompt in `agent/reviewer.py`'s `SYSTEM_PROMPTS`, (5) API endpoints in `server.py` if the web UI should surface it, and (6) a frontend page in `web/app/`.
- **Notes ingestion (MIMIC):** Notes are emitted by `MimicBigQuerySource` (`mimiciii_notes.noteevents`) and `MimicIvBigQuerySource` (`mimiciv_note.discharge`, optionally `mimiciv_note.radiology` with `--include-radiology`) as a custom `Note` resource (not FHIR `DocumentReference`) and parsed into the `notes` table. The MIMIC-IV source maps `note_type` ('DS', 'RR') back to category strings ("Discharge summary", "Radiology") so the agent's `_is_key_note` filter picks them up. Only `Note` resources go to the notes table — FHIR sources don't currently emit them because real EHRs deliver narrative via C-CDA, which the agent doesn't yet consume.
- **`MORTALITY_LOOKBACK_DAYS`** (default `90`) — days to look back for prior encounters when building mortality `prior_care`.
- **Readmission identification** (configurable in `pairs.py`):
  - `READMISSION_WINDOW_DAYS` (default `30`)
  - `READMISSION_MIN_DAYS` (default `2`) — gaps below this are treated as transfers; set to `0` for old behavior.
  - `READMISSION_EXCLUDE_TRANSFER_DISPOSITIONS` (default `true`)
  - `READMISSION_EXCLUDE_SURGICAL_FOLLOWUP` (default `true`)
  - Same `reason_code` on both encounters auto-skipped (planned cycles).
- **Encounter type normalization** at ingest time: FHIR class code `IMP` → `inpatient`, `AMB` → `ambulatory`, `EMER` → `emergency`. MIMIC source emits `IMP` for all admissions.
- **Discharge-disposition normalization (MIMIC):** Raw values from MIMIC-III (`SNF-MEDICAID ONLY CERTIF`, `REHAB/DISTINCT PART HOSP`, `LONG TERM CARE HOSPITAL`, `DEAD/EXPIRED`) and MIMIC-IV (`SKILLED NURSING FACILITY`, `REHAB`, `CHRONIC/LONG TERM ACUTE CARE`, `DIED`) are each mapped at the source to phrases that `pairs.py`'s transfer-disposition matcher and `mortality.py`'s death-disposition matcher recognize (`Skilled Nursing Facility (SNF)`, `Rehabilitation Facility`, `Long Term Care Hospital`, `Expired`, etc.).
- **Rate limiting:** `POST /api/reviews/{id}/run` is rate-limited to `REVIEW_RATE_LIMIT` per minute (default 10). Returns 429 when exceeded.
- **Error handling:** Global exception handler in `server.py` returns `{"detail": "Internal server error"}` (500) without leaking stack traces; structured logging captures the full exception for debugging.

## DUA notice (MIMIC-III / MIMIC-IV)

When ingesting from MIMIC-III or MIMIC-IV, the database contains PhysioNet *restricted* data under DUA v1.5. The credentialed user is responsible for keeping it in restricted infrastructure: single-user DB, no public deployment, no shared credentials, no committing fixtures or sample rows. Output files (`output/reviews/*`) carry the same restriction. Use a dedicated Postgres database for MIMIC ingest — don't co-mingle with Synthea or other workloads. The two MIMIC versions namespace their resource ids (`m3-` vs `m4-`) so they cannot collide if a database is mixed, but separate databases are still recommended.
