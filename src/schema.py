"""Shared PostgreSQL schema for the readmissions review agent.

This is the contract between all modules:
- src/data/ writes patient, encounter, condition, medication, observation, procedure, care_plan, and readmission_pair rows
- src/agent/ reads readmission_pairs + clinical data, writes reviews
- src/analytics/ reads reviews for cohort analysis
"""

import os

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/readmissions")

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

CREATE TABLE IF NOT EXISTS readmission_pairs (
    id SERIAL PRIMARY KEY,
    index_encounter_id TEXT NOT NULL,
    readmission_encounter_id TEXT NOT NULL,
    days_between INTEGER NOT NULL,
    patient_id TEXT NOT NULL,
    FOREIGN KEY (index_encounter_id) REFERENCES encounters(id) DEFERRABLE,
    FOREIGN KEY (readmission_encounter_id) REFERENCES encounters(id) DEFERRABLE,
    FOREIGN KEY (patient_id) REFERENCES patients(id) DEFERRABLE
);

CREATE TABLE IF NOT EXISTS reviews (
    id SERIAL PRIMARY KEY,
    pair_id INTEGER NOT NULL UNIQUE,
    structured_json TEXT,
    clinical_narrative TEXT,
    model_used TEXT,
    created_at TEXT,
    tokens_used INTEGER,
    FOREIGN KEY (pair_id) REFERENCES readmission_pairs(id) DEFERRABLE
);
"""

# Table names in FK-safe order (parents before children) for truncation
ALL_TABLES = [
    "reviews", "readmission_pairs", "care_plans", "procedures",
    "observations", "medications", "conditions", "encounters", "patients",
]


def get_connection(database_url: str | None = None) -> psycopg.Connection:
    """Get a PostgreSQL connection with the schema initialized."""
    url = database_url or DATABASE_URL
    conn = psycopg.connect(url, row_factory=dict_row)
    conn.execute(SCHEMA_SQL)
    conn.commit()
    return conn
