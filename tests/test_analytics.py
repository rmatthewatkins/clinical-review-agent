"""Tests for src/analytics/analyze.py.

Uses an in-memory SQLite database populated with synthetic data that
mirrors the schema defined in src.schema.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.schema import SCHEMA_SQL
from src.analytics.analyze import (
    _fetch_reviews,
    root_cause_distribution,
    mean_preventability_by_diagnosis,
    top_contributing_factors,
    top_recommended_interventions,
    preventability_score_distribution,
    plot_root_cause_distribution,
    plot_preventability_by_diagnosis,
    plot_contributing_factors,
    plot_recommended_interventions,
    plot_preventability_histogram,
    build_summary,
    run_analyze,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SYNTHETIC_REVIEWS = [
    {
        "pair_id": 1,
        "root_cause_category": "Medication non-adherence",
        "preventability_score": 4,
        "preventability_rationale": "Patient did not fill prescriptions.",
        "contributing_factors": ["Lack of follow-up", "Medication cost"],
        "recommended_interventions": ["Medication reconciliation", "Patient education"],
        "confidence_level": "high",
    },
    {
        "pair_id": 2,
        "root_cause_category": "Inadequate discharge planning",
        "preventability_score": 3,
        "preventability_rationale": "Discharge instructions were incomplete.",
        "contributing_factors": ["Lack of follow-up", "Poor care coordination"],
        "recommended_interventions": ["Structured discharge checklist", "Patient education"],
        "confidence_level": "medium",
    },
    {
        "pair_id": 3,
        "root_cause_category": "Medication non-adherence",
        "preventability_score": 5,
        "preventability_rationale": "Multiple missed doses documented.",
        "contributing_factors": ["Medication cost", "Health literacy"],
        "recommended_interventions": ["Medication reconciliation", "Financial counseling"],
        "confidence_level": "high",
    },
    {
        "pair_id": 4,
        "root_cause_category": "Disease progression",
        "preventability_score": 2,
        "preventability_rationale": "Natural disease progression despite treatment.",
        "contributing_factors": ["Comorbidity burden", "Lack of follow-up"],
        "recommended_interventions": ["Palliative care consult", "Care coordination"],
        "confidence_level": "medium",
    },
    {
        "pair_id": 5,
        "root_cause_category": "Inadequate discharge planning",
        "preventability_score": 4,
        "preventability_rationale": "No outpatient appointment scheduled.",
        "contributing_factors": ["Poor care coordination", "System fragmentation"],
        "recommended_interventions": ["Structured discharge checklist", "Transitional care program"],
        "confidence_level": "high",
    },
    {
        "pair_id": 6,
        "root_cause_category": "Medication non-adherence",
        "preventability_score": 3,
        "preventability_rationale": "Partial adherence noted.",
        "contributing_factors": ["Health literacy", "Medication cost"],
        "recommended_interventions": ["Patient education", "Simplified regimen"],
        "confidence_level": "low",
    },
    {
        "pair_id": 7,
        "root_cause_category": "Social determinants",
        "preventability_score": 1,
        "preventability_rationale": "Housing instability drove readmission.",
        "contributing_factors": ["Housing instability", "Lack of social support"],
        "recommended_interventions": ["Social work referral", "Community resources"],
        "confidence_level": "medium",
    },
]

DIAGNOSES = [
    (1, "Congestive heart failure"),
    (2, "Congestive heart failure"),
    (3, "COPD"),
    (4, "COPD"),
    (5, "Pneumonia"),
    (6, "Congestive heart failure"),
    (7, "Pneumonia"),
]


def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with schema and synthetic data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)

    # Insert patients
    conn.execute(
        "INSERT INTO patients (id, birth_date, gender) VALUES (?, ?, ?)",
        ("p1", "1950-01-01", "male"),
    )

    # For each review, create encounters, readmission_pairs, conditions, and reviews
    for i, (pair_id, diagnosis) in enumerate(DIAGNOSES, start=1):
        index_enc_id = f"enc-index-{pair_id}"
        readm_enc_id = f"enc-readm-{pair_id}"

        # Encounters (ignore duplicates for same pair_id via OR IGNORE)
        conn.execute(
            "INSERT OR IGNORE INTO encounters (id, patient_id, type, start, end) "
            "VALUES (?, 'p1', 'inpatient', '2025-01-01', '2025-01-05')",
            (index_enc_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO encounters (id, patient_id, type, start, end) "
            "VALUES (?, 'p1', 'inpatient', '2025-01-20', '2025-01-25')",
            (readm_enc_id,),
        )

        # Readmission pair
        conn.execute(
            "INSERT OR IGNORE INTO readmission_pairs (id, index_encounter_id, readmission_encounter_id, days_between, patient_id) "
            "VALUES (?, ?, ?, 15, 'p1')",
            (pair_id, index_enc_id, readm_enc_id),
        )

        # Condition on the index encounter
        conn.execute(
            "INSERT OR IGNORE INTO conditions (id, patient_id, encounter_id, code, display, onset, clinical_status) "
            "VALUES (?, 'p1', ?, ?, ?, '2025-01-01', 'active')",
            (f"cond-{pair_id}", index_enc_id, f"code-{pair_id}", diagnosis),
        )

        # Review
        review_data = SYNTHETIC_REVIEWS[i - 1]
        structured = json.dumps({
            "root_cause_category": review_data["root_cause_category"],
            "preventability_score": review_data["preventability_score"],
            "preventability_rationale": review_data["preventability_rationale"],
            "contributing_factors": review_data["contributing_factors"],
            "recommended_interventions": review_data["recommended_interventions"],
            "confidence_level": review_data["confidence_level"],
        })
        conn.execute(
            "INSERT INTO reviews (pair_id, structured_json, clinical_narrative, model_used, created_at, tokens_used) "
            "VALUES (?, ?, 'narrative', 'test-model', '2025-02-01', 100)",
            (pair_id, structured),
        )

    conn.commit()
    return conn


@pytest.fixture
def db():
    conn = _make_db()
    yield conn
    conn.close()


@pytest.fixture
def reviews(db):
    return _fetch_reviews(db)


# ---------------------------------------------------------------------------
# Tests: analytic computations
# ---------------------------------------------------------------------------


class TestRootCauseDistribution:
    def test_counts(self, reviews):
        dist = root_cause_distribution(reviews)
        assert dist["Medication non-adherence"]["count"] == 3
        assert dist["Inadequate discharge planning"]["count"] == 2
        assert dist["Disease progression"]["count"] == 1
        assert dist["Social determinants"]["count"] == 1

    def test_percentages_sum_to_100(self, reviews):
        dist = root_cause_distribution(reviews)
        total_pct = sum(v["percentage"] for v in dist.values())
        assert abs(total_pct - 100.0) < 0.5  # allow rounding tolerance


class TestMeanPreventabilityByDiagnosis:
    def test_means(self, db, reviews):
        result = mean_preventability_by_diagnosis(db, reviews)
        # CHF: scores 4, 3, 3 -> mean 3.33
        assert abs(result["Congestive heart failure"] - 3.33) < 0.05
        # COPD: scores 5, 2 -> mean 3.5
        assert abs(result["COPD"] - 3.5) < 0.05
        # Pneumonia: scores 4, 1 -> mean 2.5
        assert abs(result["Pneumonia"] - 2.5) < 0.05

    def test_all_diagnoses_present(self, db, reviews):
        result = mean_preventability_by_diagnosis(db, reviews)
        assert len(result) == 3


class TestTopContributingFactors:
    def test_ranking(self, reviews):
        factors = top_contributing_factors(reviews)
        factor_dict = dict(factors)
        # "Lack of follow-up" appears in reviews 1, 2, 4 -> count 3
        assert factor_dict["Lack of follow-up"] == 3
        # "Medication cost" appears in reviews 1, 3, 6 -> count 3
        assert factor_dict["Medication cost"] == 3

    def test_top_n_limit(self, reviews):
        factors = top_contributing_factors(reviews, top_n=3)
        assert len(factors) <= 3


class TestTopRecommendedInterventions:
    def test_ranking(self, reviews):
        interventions = top_recommended_interventions(reviews)
        interv_dict = dict(interventions)
        # "Patient education" appears in reviews 1, 2, 6 -> count 3
        assert interv_dict["Patient education"] == 3
        # "Medication reconciliation" appears in reviews 1, 3 -> count 2
        assert interv_dict["Medication reconciliation"] == 2


class TestPreventabilityScoreDistribution:
    def test_all_scores_present(self, reviews):
        dist = preventability_score_distribution(reviews)
        assert set(dist.keys()) == {1, 2, 3, 4, 5}

    def test_counts(self, reviews):
        dist = preventability_score_distribution(reviews)
        assert dist[1] == 1  # review 7
        assert dist[2] == 1  # review 4
        assert dist[3] == 2  # reviews 2, 6
        assert dist[4] == 2  # reviews 1, 5
        assert dist[5] == 1  # review 3


# ---------------------------------------------------------------------------
# Tests: visualizations
# ---------------------------------------------------------------------------


class TestVisualizations:
    def test_all_plots_created(self, db, reviews):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            root_causes = root_cause_distribution(reviews)
            prev_by_diag = mean_preventability_by_diagnosis(db, reviews)
            factors = top_contributing_factors(reviews)
            interventions = top_recommended_interventions(reviews)
            score_dist = preventability_score_distribution(reviews)

            plot_root_cause_distribution(root_causes, out)
            plot_preventability_by_diagnosis(prev_by_diag, out)
            plot_contributing_factors(factors, out)
            plot_recommended_interventions(interventions, out)
            plot_preventability_histogram(score_dist, out)

            expected_files = [
                "root_cause_distribution.png",
                "preventability_by_diagnosis.png",
                "contributing_factors.png",
                "recommended_interventions.png",
                "preventability_histogram.png",
            ]
            for fname in expected_files:
                fpath = out / fname
                assert fpath.exists(), f"{fname} was not created"
                assert fpath.stat().st_size > 0, f"{fname} is empty"


# ---------------------------------------------------------------------------
# Tests: summary JSON
# ---------------------------------------------------------------------------


class TestBuildSummary:
    def test_summary_structure(self, db, reviews):
        root_causes = root_cause_distribution(reviews)
        prev_by_diag = mean_preventability_by_diagnosis(db, reviews)
        factors = top_contributing_factors(reviews)
        interventions = top_recommended_interventions(reviews)
        score_dist = preventability_score_distribution(reviews)

        summary = build_summary(root_causes, prev_by_diag, factors, interventions, score_dist, len(reviews))
        assert summary["total_reviews"] == 7
        assert "root_cause_distribution" in summary
        assert "mean_preventability_by_diagnosis" in summary
        assert "top_contributing_factors" in summary
        assert "top_recommended_interventions" in summary
        assert "preventability_score_distribution" in summary

    def test_summary_serializable(self, db, reviews):
        root_causes = root_cause_distribution(reviews)
        prev_by_diag = mean_preventability_by_diagnosis(db, reviews)
        factors = top_contributing_factors(reviews)
        interventions = top_recommended_interventions(reviews)
        score_dist = preventability_score_distribution(reviews)

        summary = build_summary(root_causes, prev_by_diag, factors, interventions, score_dist, len(reviews))
        # Should not raise
        text = json.dumps(summary, indent=2)
        parsed = json.loads(text)
        assert parsed["total_reviews"] == 7


# ---------------------------------------------------------------------------
# Tests: run_analyze entrypoint
# ---------------------------------------------------------------------------


class TestRunAnalyze:
    def test_empty_reviews_graceful(self, capsys):
        """run_analyze should print a friendly message and return when no reviews exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty.db"
            out = Path(tmpdir) / "output"
            out.mkdir()
            run_analyze(db_path=db_path, output_dir=out)
            captured = capsys.readouterr()
            assert "No completed reviews found" in captured.out

    def test_full_run(self):
        """run_analyze should produce all outputs when reviews exist."""
        conn = _make_db()
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write the in-memory DB to a file so run_analyze can open it
            db_path = Path(tmpdir) / "test.db"
            file_conn = sqlite3.connect(str(db_path))
            conn.backup(file_conn)
            file_conn.close()
            conn.close()

            out = Path(tmpdir) / "output"
            out.mkdir()
            run_analyze(db_path=db_path, output_dir=out)

            # Check all expected outputs
            assert (out / "summary.json").exists()
            summary = json.loads((out / "summary.json").read_text())
            assert summary["total_reviews"] == 7

            for fname in [
                "root_cause_distribution.png",
                "preventability_by_diagnosis.png",
                "contributing_factors.png",
                "recommended_interventions.png",
                "preventability_histogram.png",
            ]:
                assert (out / fname).exists(), f"{fname} missing"
