"""Shared SQLite schema for the readmissions review agent.

This is the contract between all modules:
- src/data/ writes patient, encounter, condition, medication, observation, procedure, care_plan, and readmission_pair rows
- src/agent/ reads readmission_pairs + clinical data, writes reviews
- src/analytics/ reads reviews for cohort analysis
"""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "readmissions.db"

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
    end TEXT,
    reason_code TEXT,
    reason_display TEXT,
    discharge_disposition TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id)
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
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (encounter_id) REFERENCES encounters(id)
);

CREATE TABLE IF NOT EXISTS medications (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    code TEXT,
    display TEXT,
    start TEXT,
    end TEXT,
    status TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (encounter_id) REFERENCES encounters(id)
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
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (encounter_id) REFERENCES encounters(id)
);

CREATE TABLE IF NOT EXISTS procedures (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    code TEXT,
    display TEXT,
    date TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (encounter_id) REFERENCES encounters(id)
);

CREATE TABLE IF NOT EXISTS care_plans (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    code TEXT,
    display TEXT,
    start TEXT,
    end TEXT,
    status TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (encounter_id) REFERENCES encounters(id)
);

CREATE TABLE IF NOT EXISTS readmission_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    index_encounter_id TEXT NOT NULL,
    readmission_encounter_id TEXT NOT NULL,
    days_between INTEGER NOT NULL,
    patient_id TEXT NOT NULL,
    FOREIGN KEY (index_encounter_id) REFERENCES encounters(id),
    FOREIGN KEY (readmission_encounter_id) REFERENCES encounters(id),
    FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id INTEGER NOT NULL UNIQUE,
    structured_json TEXT,
    clinical_narrative TEXT,
    model_used TEXT,
    created_at TEXT,
    tokens_used INTEGER,
    FOREIGN KEY (pair_id) REFERENCES readmission_pairs(id)
);
"""


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with the schema initialized."""
    db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    return conn
