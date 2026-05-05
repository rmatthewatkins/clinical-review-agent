"""Shared PostgreSQL schema for the readmissions review agent.

This is the contract between all modules:
- src/data/ writes patient, encounter, condition, medication, observation, procedure, care_plan, and readmission rows
- src/agent/ reads readmissions + clinical data, writes reviews
- src/analytics/ reads reviews for cohort analysis
"""

import os

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Migration: rename readmission_pairs → readmissions, pair_id → readmission_id
MIGRATION_SQL = """
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'readmission_pairs') THEN
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'reviews' AND column_name = 'pair_id') THEN
            ALTER TABLE reviews RENAME COLUMN pair_id TO readmission_id;
        END IF;
        ALTER TABLE readmission_pairs RENAME TO readmissions;
    END IF;
END $$;

-- Generalize reviews table: add case_type + case_id columns
-- Guarded with IF EXISTS so a fresh DB (where SCHEMA_SQL hasn't run yet)
-- doesn't fail on these migrations; SCHEMA_SQL below creates the table
-- with these columns already present.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'reviews') THEN
        ALTER TABLE reviews ADD COLUMN IF NOT EXISTS case_type TEXT;
        ALTER TABLE reviews ADD COLUMN IF NOT EXISTS case_id INTEGER;

        -- Backfill existing readmission reviews
        UPDATE reviews SET case_type = 'readmission', case_id = readmission_id
        WHERE case_type IS NULL AND readmission_id IS NOT NULL;

        -- Unique index for upserts by (case_type, case_id)
        CREATE UNIQUE INDEX IF NOT EXISTS reviews_case_type_case_id_idx
        ON reviews (case_type, case_id);
    END IF;
END $$;

-- Drop NOT NULL on readmission_id (now superseded by case_type + case_id)
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'reviews' AND column_name = 'readmission_id'
               AND is_nullable = 'NO') THEN
        ALTER TABLE reviews ALTER COLUMN readmission_id DROP NOT NULL;
    END IF;
END $$;

-- Drop old unique constraint on readmission_id if it exists
-- 'reviews'::regclass is resolved at parse time, so the table-exists guard
-- has to be a nested IF (not an AND) to short-circuit before the cast.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'reviews') THEN
        IF EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'reviews'::regclass AND conname = 'reviews_readmission_id_key') THEN
            ALTER TABLE reviews DROP CONSTRAINT reviews_readmission_id_key;
        END IF;
    END IF;
END $$;
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    birth_date TEXT,
    gender TEXT,
    race TEXT,
    ethnicity TEXT,
    city TEXT,
    state TEXT,
    lat REAL,
    lng REAL
);

CREATE TABLE IF NOT EXISTS encounters (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    type TEXT,
    start TEXT,
    "end" TEXT,
    reason_code TEXT,
    reason_display TEXT,
    discharge_disposition TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id) DEFERRABLE
);

CREATE TABLE IF NOT EXISTS conditions (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    code TEXT,
    display TEXT,
    onset TEXT,
    abatement TEXT,
    clinical_status TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id) DEFERRABLE,
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) DEFERRABLE
);

CREATE TABLE IF NOT EXISTS medications (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    code TEXT,
    display TEXT,
    start TEXT,
    "end" TEXT,
    status TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id) DEFERRABLE,
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) DEFERRABLE
);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    code TEXT,
    display TEXT,
    value TEXT,
    unit TEXT,
    date TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id) DEFERRABLE,
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) DEFERRABLE
);

CREATE TABLE IF NOT EXISTS procedures (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    code TEXT,
    display TEXT,
    date TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id) DEFERRABLE,
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) DEFERRABLE
);

CREATE TABLE IF NOT EXISTS care_plans (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    code TEXT,
    display TEXT,
    start TEXT,
    "end" TEXT,
    status TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id) DEFERRABLE,
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) DEFERRABLE
);

-- Notes carry the free-text narrative that doesn't ride on FHIR R4
-- (real EHR integrations get this via C-CDA, not DocumentReference).
-- MIMIC-III hands us the raw text directly via mimiciii_notes.noteevents.
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    category TEXT,
    description TEXT,
    chartdate TEXT,
    charttime TEXT,
    text TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id) DEFERRABLE,
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) DEFERRABLE
);

CREATE INDEX IF NOT EXISTS notes_encounter_idx ON notes (encounter_id);
CREATE INDEX IF NOT EXISTS notes_patient_idx ON notes (patient_id);

CREATE TABLE IF NOT EXISTS readmissions (
    id SERIAL PRIMARY KEY,
    index_encounter_id TEXT NOT NULL,
    readmission_encounter_id TEXT NOT NULL,
    days_between INTEGER NOT NULL,
    patient_id TEXT NOT NULL,
    FOREIGN KEY (index_encounter_id) REFERENCES encounters(id) DEFERRABLE,
    FOREIGN KEY (readmission_encounter_id) REFERENCES encounters(id) DEFERRABLE,
    FOREIGN KEY (patient_id) REFERENCES patients(id) DEFERRABLE
);

CREATE TABLE IF NOT EXISTS mortality_cases (
    id SERIAL PRIMARY KEY,
    encounter_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    death_date TEXT,
    lookback_days INTEGER NOT NULL DEFAULT 90,
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) DEFERRABLE,
    FOREIGN KEY (patient_id) REFERENCES patients(id) DEFERRABLE
);

CREATE TABLE IF NOT EXISTS reviews (
    id SERIAL PRIMARY KEY,
    case_type TEXT NOT NULL DEFAULT 'readmission',
    case_id INTEGER NOT NULL DEFAULT 0,
    readmission_id INTEGER,
    structured_json TEXT,
    clinical_narrative TEXT,
    model_used TEXT,
    created_at TEXT,
    tokens_used INTEGER
);
"""

# Table names in FK-safe order (children before parents) for truncation
ALL_TABLES = [
    "reviews", "mortality_cases", "readmissions", "notes", "care_plans",
    "procedures", "observations", "medications", "conditions", "encounters",
    "patients",
]


def get_connection(database_url: str | None = None) -> psycopg.Connection:
    """Get a PostgreSQL connection with the schema initialized.

    Raises ``RuntimeError`` if no database URL is available.
    """
    url = database_url or DATABASE_URL
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is required. "
            "Set it before running: export DATABASE_URL=postgresql://..."
        )
    conn = psycopg.connect(url, row_factory=dict_row)
    conn.execute(MIGRATION_SQL)
    conn.execute(SCHEMA_SQL)
    conn.commit()
    return conn
