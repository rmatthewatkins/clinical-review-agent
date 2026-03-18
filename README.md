# Readmission Review Agent

An AI-powered clinical readmissions review agent that ingests FHIR patient data — from synthetic sources (Synthea) or production FHIR R4 servers (Epic, HAPI) — to identify and assess 30-day hospital readmissions. Uses Claude as a simulated hospitalist to perform structured peer reviews with root cause analysis, preventability scoring, and intervention recommendations.

## Architecture

```
src/
├── schema.py                # Shared PostgreSQL schema (9 tables) — the contract between all modules
├── data/
│   ├── sources/             # Pluggable FHIR data source adapters
│   │   ├── base.py          # FhirSource protocol + shared FHIR parsing helpers
│   │   ├── synthea_file.py  # File-based source (Synthea Bundle JSONs)
│   │   └── fhir_api.py      # FHIR R4 REST API client (Epic, HAPI, etc.)
│   ├── ingest.py            # Source-agnostic FHIR parser → PostgreSQL
│   └── pairs.py             # 30-day readmission pair identifier (filters planned readmissions)
├── agent/
│   ├── context.py           # Clinical context assembler from DB
│   └── reviewer.py          # Claude API integration + structured peer review
└── analytics/
    └── analyze.py           # Cohort-level analytics + matplotlib visualizations

server.py                    # FastAPI REST API backend (port 8000)
web/                         # Next.js + TypeScript + Tailwind frontend (port 3000)
├── app/
│   ├── page.tsx             # Dashboard — summary metrics, charts, quick links
│   ├── readmissions/        # All readmission pairs with review status
│   ├── reviews/             # Completed reviews list + detail view with AI review trigger
│   └── analytics/           # Multi-dimension analytics (root cause, diagnosis, gender, age, etc.)
└── lib/api.ts               # API client + TypeScript types
```

### Pipeline

```
FHIR Source (files or API) → ingest → PostgreSQL → identify-pairs → review (Claude) → analyze
```

1. **Ingest** accepts any FHIR data source — Synthea JSON files on disk or a FHIR R4 REST API (Epic, HAPI) — and normalizes resources (Patient, Encounter, Condition, MedicationRequest, Observation, Procedure, CarePlan) into PostgreSQL (hosted on Railway).
2. **Identify Pairs** finds inpatient encounters followed by another inpatient encounter for the same patient within 30 days of discharge. Planned readmissions (e.g., recurring chemotherapy cycles with identical reason codes) are automatically filtered out.
3. **Review** assembles a clinical context package for each pair — index admission details, interval care, readmission presentation, and patient baseline — then sends it to Claude for structured peer review.
4. **Analyze** computes cohort-level metrics across completed reviews and generates visualizations.

## Prerequisites

- Python 3.11+
- Node.js 18+ (for the web UI)
- Java (for running Synthea)
- An Anthropic API key

## Setup

```bash
# Clone the repo
git clone <repo-url> && cd readmission-review-agent

# Create virtual environment and install Python dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install typer anthropic matplotlib rich httpx "psycopg[binary]" fastapi "uvicorn[standard]"

# Install dev dependencies for testing
pip install pytest pytest-cov

# Install frontend dependencies
cd web && npm install && cd ..

# Set your API key and database URL
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
echo 'DATABASE_URL=postgresql://...' >> .env
```

## Generating Synthetic Data

This project uses [Synthea](https://github.com/synthetichealth/synthea) to generate realistic synthetic FHIR patient data. Synthea is a Java application that must be downloaded and run separately.

```bash
# Clone and build Synthea
git clone https://github.com/synthetichealth/synthea.git /tmp/synthea
cd /tmp/synthea
./gradlew build check -x test

# Generate patients — use -a 50-90 for older patients with more inpatient encounters
./run_synthea -p 500 -a 50-90

# Output will be in /tmp/synthea/output/fhir/
```

Younger populations generate very few inpatient encounters. For meaningful readmission analysis, generate at least 500 patients in the 50-90 age range.

## Usage

Source your `.env` file before running commands that call the Claude API:

```bash
source .venv/bin/activate
set -a && source .env && set +a
```

### 1. Ingest FHIR data

```bash
# From Synthea files on disk
python main.py ingest --dir /tmp/synthea/output/fhir

# From a FHIR R4 server (e.g., HAPI, Epic)
python main.py ingest --fhir-url http://localhost:8080/fhir
python main.py ingest --fhir-url https://fhir.epic.com/.../R4 --token "$FHIR_TOKEN"
```

Normalizes FHIR resources and loads them into PostgreSQL. The FHIR API client handles pagination, auth, and rate limiting automatically.

### 2. Identify readmission pairs

```bash
python main.py identify-pairs
```

Finds all 30-day inpatient readmission pairs. Automatically excludes planned readmissions (encounters with identical reason codes, such as scheduled chemotherapy cycles).

### 3. Run AI reviews

```bash
# Preview the assembled context for the first unreviewed pair (no API call)
python main.py review --dry-run

# Review a specific number of pairs
python main.py review --limit 10

# Review all remaining pairs
python main.py review
```

Each review produces:
- **Structured JSON** — root cause category, preventability score (1-5), contributing factors, recommended interventions, confidence level
- **Clinical narrative** — 2-3 paragraph physician-style prose review

Results are stored in PostgreSQL and written to `output/reviews/` as individual `.json` and `.md` files.

### 4. Generate analytics

```bash
python main.py analyze
```

Produces cohort-level analysis in `output/analytics/`:
- `root_cause_distribution.png` — distribution of root cause categories
- `preventability_by_diagnosis.png` — mean preventability score by diagnosis group
- `contributing_factors.png` — most common contributing factors
- `recommended_interventions.png` — most common recommended interventions
- `preventability_histogram.png` — preventability score distribution (1-5)
- `summary.json` — all computed metrics

### 5. Start the web UI

```bash
# Terminal 1 — FastAPI backend
set -a && source .env && set +a
python server.py                    # http://localhost:8000

# Terminal 2 — Next.js frontend
cd web && npm run dev               # http://localhost:3000
```

The web UI provides:
- **Dashboard** — Summary metrics (patients, encounters, pairs, reviews), root cause distribution, preventability charts, quick navigation links
- **Readmissions** — All identified readmission pairs with patient demographics, encounter details, and review status (Reviewed/Pending). Sortable and searchable.
- **Reviews** — Completed reviews with colored preventability badges, root cause pills, and confidence indicators. Click any review to see the full detail.
- **Review Detail** — Two-column deep-dive: clinical context (left) and AI review (right). Includes a **"Run AI Review"** button to trigger Claude review directly from the browser.
- **Analytics** — Multi-dimension analysis across 7 tabs: by root cause, diagnosis, gender, age group, days to readmission, contributing factors, and recommended interventions.

## Review Schema

Each AI review produces a structured assessment with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `root_cause_category` | enum | `premature_discharge`, `inadequate_transition_planning`, `medication_related`, `inadequate_follow_up`, `disease_progression`, `social_determinants`, `patient_behavioral`, `unavoidable`, `other` |
| `preventability_score` | 1-5 | 1 = clearly not preventable, 5 = clearly preventable |
| `preventability_rationale` | string | Evidence-based explanation of the score |
| `contributing_factors` | string[] | Specific factors that contributed to readmission |
| `recommended_interventions` | string[] | Actionable interventions to prevent similar readmissions |
| `confidence_level` | enum | `high`, `moderate`, `low` |

## Database Schema

The PostgreSQL database (hosted on Railway) contains 9 tables:

- **patients** — demographics, location, race/ethnicity
- **encounters** — type, dates, reason, discharge disposition
- **conditions** — diagnoses with onset/abatement and clinical status
- **medications** — prescriptions with status and dates
- **observations** — lab values, vitals, screening scores
- **procedures** — performed procedures with dates
- **care_plans** — active care plans with status
- **readmission_pairs** — identified index → readmission encounter pairs with days between
- **reviews** — AI-generated structured reviews and clinical narratives

## Testing

```bash
python -m pytest tests/ -v
```

Tests cover:
- FHIR bundle parsing and PostgreSQL loading (`test_data.py`)
- Readmission pair identification with edge cases (`test_data.py`)
- Clinical context assembly (`test_agent.py`)
- Claude response parsing (`test_agent.py`)
- Analytics computations and visualization generation (`test_analytics.py`)

## Configuration

| Setting | Location | Default |
|---------|----------|---------|
| Claude model | `src/agent/reviewer.py` | `claude-sonnet-4-6` |
| Database URL | `src/schema.py` | `DATABASE_URL` env var (Railway PostgreSQL) |
| Review output | `src/agent/reviewer.py` | `output/reviews/` |
| Analytics output | `src/analytics/analyze.py` | `output/analytics/` |
| Readmission window | `src/data/pairs.py` | 30 days |
| API retry (max) | `src/agent/reviewer.py` | 5 retries, exponential backoff |
| FHIR API page size | `src/data/sources/fhir_api.py` | 100 (`_count`) |
| FHIR token env var | `main.py` | `FHIR_TOKEN` |
