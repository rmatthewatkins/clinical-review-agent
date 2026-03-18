# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup — Python backend
python3 -m venv .venv && source .venv/bin/activate
pip install typer anthropic matplotlib rich httpx pytest pytest-cov "psycopg[binary]" fastapi "uvicorn[standard]"

# Setup — Next.js frontend
cd web && npm install && cd ..

# Run tests (uses local postgresql://localhost/readmissions_test by default)
python -m pytest tests/ -v
python -m pytest tests/test_data.py -v              # just data pipeline tests
python -m pytest tests/test_sources.py -v            # source adapters + Epic compat
python -m pytest tests/test_agent.py::TestResponseParsing -v  # single test class
python -m pytest tests/test_analytics.py -k "test_counts" -v  # by name pattern

# CLI commands (source .env first for API calls + DATABASE_URL)
set -a && source .env && set +a
python main.py ingest --dir <synthea-fhir-dir>       # file-based ingest
python main.py ingest --fhir-url http://localhost:8080/fhir  # FHIR API ingest
python main.py identify-pairs
python main.py review --dry-run          # preview context, no API call
python main.py review --limit N
python main.py analyze

# Start the web app (two terminals)
set -a && source .env && set +a && python server.py   # FastAPI on :8000
cd web && npm run dev                                  # Next.js on :3000

# Generate synthetic data (requires Java)
cd /tmp/synthea && ./run_synthea -p 500 -a 50-90
```

## Architecture

PostgreSQL (deployed on Railway) is the shared contract between all modules. `src/schema.py` defines 9 tables and `get_connection()` returns an initialized psycopg connection with `dict_row` factory. All modules import from `src.schema`. Connection is configured via `DATABASE_URL` environment variable.

**Pipeline flow:** `source adapter → ingest (parse + load) → identify-pairs → review → analyze`

### Web UI

- **`server.py`** — FastAPI backend (port 8000). Uses thread-local psycopg connections to Railway PostgreSQL. Provides REST API for the frontend.
- **`web/`** — Next.js 16 + TypeScript + Tailwind CSS frontend (port 3000). App Router with 4 pages: Dashboard, Readmissions, Reviews, Analytics.
- **API endpoints:** `GET /api/summary`, `GET /api/pairs`, `GET /api/reviews`, `GET /api/reviews/{id}`, `POST /api/reviews/{id}/run` (triggers AI review), `GET /api/analytics`, `GET /api/analytics/by-root-cause`, `GET /api/analytics/by-diagnosis`, `GET /api/analytics/by-gender`, `GET /api/analytics/by-age-group`, `GET /api/analytics/by-days-between`.

### Data Source Layer (`src/data/sources/`)

The `FhirSource` protocol (`base.py`) defines a single method: `iter_resources() -> Iterator[dict]`. Two implementations:

- **`SyntheaFileSource`** (`synthea_file.py`) — Reads FHIR Bundle JSON files from a directory. Used for dev/test with Synthea-generated data.
- **`FhirApiSource`** (`fhir_api.py`) — Queries a FHIR R4 REST API (Epic, HAPI, etc.). Handles pagination, bearer token auth via callable `token_provider`, rate limiting with exponential backoff. Designed for production use with Epic.

Shared FHIR parsing helpers live in `base.py`: `ref()` (strips reference prefixes — handles `urn:uuid:`, `Patient/id`, and full URLs), `code_display()` (extracts from CodeableConcept), `extension_value()` (reads US Core extensions).

### Ingest + Parsers (`src/data/ingest.py`)

`run_ingest(source)` accepts any `FhirSource`, iterates resources, routes each through `_parse_*` functions, and batch-inserts into PostgreSQL. Uses `SET CONSTRAINTS ALL DEFERRED` during bulk load and `ON CONFLICT DO NOTHING` for idempotency. The parsers handle both Synthea and Epic FHIR patterns:
- `_parse_medication`: falls back from `medicationCodeableConcept` to `medicationReference.display`
- `_parse_encounter`: falls back from `reasonCode` to `reasonReference`
- `_parse_observation`: handles `component` arrays (e.g., blood pressure)
- Backwards compatible: `run_ingest("path/to/dir")` still works (auto-wraps in SyntheaFileSource)

### Downstream (source-agnostic)

- **src/data/pairs.py** — Identifies 30-day inpatient readmission pairs. Filters planned readmissions by detecting identical `reason_code` on index and readmission encounters. Clears reviews and pairs before re-identifying (idempotent).

- **src/agent/context.py** — Assembles clinical context dict from a readmission_pair row with 4 sections: patient_baseline, index_admission, interval_care, readmission. `context_to_prompt_string()` serializes for the LLM.

- **src/agent/reviewer.py** — Sends context to Claude with a hospitalist peer review system prompt. Extracts structured JSON from ` ```json``` ` fences and narrative from remainder. Stores in `reviews` table + `output/reviews/`. Uses `ON CONFLICT (pair_id) DO UPDATE` for upsert. Exponential backoff on 429s.

- **src/analytics/analyze.py** — Cohort metrics + matplotlib PNGs to `output/analytics/` + `summary.json`. Must use `matplotlib.use('Agg')` before other matplotlib imports.

**CLI wiring:** `main.py` uses Typer with lazy imports. The `ingest` command accepts `--dir` (file source) or `--fhir-url` (API source) with optional `--token`.

## Key Patterns

- **Database:** PostgreSQL on Railway via `psycopg` (v3) with `dict_row` factory. Connection URL from `DATABASE_URL` env var. Schema uses `DEFERRABLE` foreign keys for bulk load support. The `"end"` column is quoted in SQL (reserved word in PostgreSQL).
- **DB in tests:** Tests default to `postgresql://localhost/readmissions_test` (NOT Railway production). `conftest.py` patches `DATABASE_URL` and provides `db_conn` fixture that truncates all tables between tests. Override with `TEST_DATABASE_URL` env var if needed.
- **Server connections:** `server.py` uses `threading.local()` for one persistent connection per uvicorn worker thread (avoids reconnect + schema DDL per request).
- **SQL placeholders:** All queries use `%s` (psycopg format).
- **API key:** Read from `ANTHROPIC_API_KEY` env var (stored in `.env`, not auto-loaded — must source before running).
- **FHIR token:** `FHIR_TOKEN` env var or `--token` flag for FHIR API auth.
- **Model:** Set as `MODEL` constant in `reviewer.py` (currently `claude-sonnet-4-6`).
- **The `reviews.structured_json` column** stores a JSON string parsed with `json.loads()`. Fields: `root_cause_category`, `preventability_score` (1-5), `preventability_rationale`, `contributing_factors` (array), `recommended_interventions` (array), `confidence_level`.
- **Readmission window** (30 days) and **planned readmission filter** (same reason_code) are in `pairs.py`.
- **Encounter type normalization** happens at ingest time: FHIR class code `IMP` → `"inpatient"`, `AMB` → `"ambulatory"`, `EMER` → `"emergency"`.
