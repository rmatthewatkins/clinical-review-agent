"""Cohort-level analytics for completed readmission reviews.

Reads reviews from PostgreSQL, computes aggregate metrics, generates
matplotlib visualizations, and writes a summary JSON report.
"""

import json
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from src.schema import get_connection  # noqa: E402

OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "analytics"


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _fetch_reviews(conn) -> list[dict]:
    """Return all reviews with their parsed structured_json."""
    rows = conn.execute(
        "SELECT id, pair_id, structured_json, clinical_narrative, "
        "model_used, created_at, tokens_used FROM reviews"
    ).fetchall()
    results = []
    for row in rows:
        rec = dict(row)
        if rec["structured_json"]:
            rec["parsed"] = json.loads(rec["structured_json"])
        else:
            rec["parsed"] = {}
        results.append(rec)
    return results


def _fetch_diagnosis_for_pair(conn, pair_id: int) -> str | None:
    """Get the primary diagnosis display for the index encounter of a pair."""
    row = conn.execute(
        """
        SELECT c.display
        FROM readmission_pairs rp
        JOIN conditions c ON c.encounter_id = rp.index_encounter_id
        WHERE rp.id = %s
        ORDER BY c.onset ASC
        LIMIT 1
        """,
        (pair_id,),
    ).fetchone()
    return row["display"] if row else None


# ---------------------------------------------------------------------------
# Analytic computations
# ---------------------------------------------------------------------------

def root_cause_distribution(reviews: list[dict]) -> dict[str, dict]:
    """Counts and percentages of each root_cause_category."""
    counts: Counter = Counter()
    for r in reviews:
        cat = r["parsed"].get("root_cause_category", "Unknown")
        counts[cat] += 1
    total = sum(counts.values()) or 1
    return {
        cat: {"count": cnt, "percentage": round(cnt / total * 100, 1)}
        for cat, cnt in counts.most_common()
    }


def mean_preventability_by_diagnosis(
    conn, reviews: list[dict]
) -> dict[str, float]:
    """Mean preventability_score grouped by primary diagnosis of the index encounter."""
    diag_scores: dict[str, list[float]] = {}
    for r in reviews:
        score = r["parsed"].get("preventability_score")
        if score is None:
            continue
        diag = _fetch_diagnosis_for_pair(conn, r["pair_id"])
        if diag is None:
            diag = "Unknown"
        diag_scores.setdefault(diag, []).append(float(score))
    return {
        diag: round(sum(scores) / len(scores), 2)
        for diag, scores in sorted(diag_scores.items(), key=lambda x: -sum(x[1]) / len(x[1]))
    }


def top_contributing_factors(reviews: list[dict], top_n: int = 15) -> list[tuple[str, int]]:
    """Parse contributing_factors arrays, count frequency, rank descending."""
    counts: Counter = Counter()
    for r in reviews:
        for factor in r["parsed"].get("contributing_factors", []):
            counts[factor] += 1
    return counts.most_common(top_n)


def top_recommended_interventions(reviews: list[dict], top_n: int = 15) -> list[tuple[str, int]]:
    """Parse recommended_interventions arrays, count frequency, rank descending."""
    counts: Counter = Counter()
    for r in reviews:
        for intervention in r["parsed"].get("recommended_interventions", []):
            counts[intervention] += 1
    return counts.most_common(top_n)


def preventability_score_distribution(reviews: list[dict]) -> dict[int, int]:
    """Counts for each preventability score 1-5."""
    counts = {i: 0 for i in range(1, 6)}
    for r in reviews:
        score = r["parsed"].get("preventability_score")
        if score is not None and int(score) in counts:
            counts[int(score)] += 1
    return counts


# ---------------------------------------------------------------------------
# Visualization helpers
# ---------------------------------------------------------------------------

_STYLE = "seaborn-v0_8"


def _apply_style():
    """Apply a clean professional style; fall back gracefully."""
    try:
        plt.style.use(_STYLE)
    except OSError:
        plt.style.use("ggplot")


def _save(fig, name: str, output_dir: Path) -> Path:
    path = output_dir / name
    fig.savefig(str(path), bbox_inches="tight", dpi=150)
    plt.close(fig)
    return path


def plot_root_cause_distribution(data: dict[str, dict], output_dir: Path) -> Path:
    _apply_style()
    categories = list(data.keys())
    counts = [data[c]["count"] for c in categories]

    fig, ax = plt.subplots(figsize=(10, max(4, len(categories) * 0.5)))
    bars = ax.barh(categories, counts, color="#4C72B0")
    ax.set_xlabel("Number of Reviews")
    ax.set_title("Root Cause Category Distribution")
    ax.invert_yaxis()
    for bar, cnt in zip(bars, counts):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                str(cnt), va="center", fontsize=9)
    fig.tight_layout()
    return _save(fig, "root_cause_distribution.png", output_dir)


def plot_preventability_by_diagnosis(data: dict[str, float], output_dir: Path, top_n: int = 10) -> Path:
    _apply_style()
    items = list(data.items())[:top_n]
    if not items:
        items = [("No data", 0)]
    labels, means = zip(*items)

    fig, ax = plt.subplots(figsize=(10, max(4, len(labels) * 0.5)))
    bars = ax.barh(list(labels), list(means), color="#55A868")
    ax.set_xlabel("Mean Preventability Score")
    ax.set_title("Mean Preventability Score by Diagnosis Group (Top 10)")
    ax.set_xlim(0, 5.5)
    ax.invert_yaxis()
    for bar, val in zip(bars, means):
        ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2,
                f"{val:.2f}", va="center", fontsize=9)
    fig.tight_layout()
    return _save(fig, "preventability_by_diagnosis.png", output_dir)


def plot_contributing_factors(data: list[tuple[str, int]], output_dir: Path) -> Path:
    _apply_style()
    if not data:
        data = [("No data", 0)]
    labels, counts = zip(*data)

    fig, ax = plt.subplots(figsize=(10, max(4, len(labels) * 0.45)))
    bars = ax.barh(list(labels), list(counts), color="#C44E52")
    ax.set_xlabel("Frequency")
    ax.set_title("Top Contributing Factors")
    ax.invert_yaxis()
    for bar, cnt in zip(bars, counts):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                str(cnt), va="center", fontsize=9)
    fig.tight_layout()
    return _save(fig, "contributing_factors.png", output_dir)


def plot_recommended_interventions(data: list[tuple[str, int]], output_dir: Path) -> Path:
    _apply_style()
    if not data:
        data = [("No data", 0)]
    labels, counts = zip(*data)

    fig, ax = plt.subplots(figsize=(10, max(4, len(labels) * 0.45)))
    bars = ax.barh(list(labels), list(counts), color="#8172B2")
    ax.set_xlabel("Frequency")
    ax.set_title("Top Recommended Interventions")
    ax.invert_yaxis()
    for bar, cnt in zip(bars, counts):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                str(cnt), va="center", fontsize=9)
    fig.tight_layout()
    return _save(fig, "recommended_interventions.png", output_dir)


def plot_preventability_histogram(data: dict[int, int], output_dir: Path) -> Path:
    _apply_style()
    scores = list(data.keys())
    counts = list(data.values())

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(scores, counts, color="#CCB974", edgecolor="white", width=0.7)
    ax.set_xlabel("Preventability Score")
    ax.set_ylabel("Number of Reviews")
    ax.set_title("Preventability Score Distribution")
    ax.set_xticks(scores)
    ax.set_xticklabels([str(s) for s in scores])
    for bar, cnt in zip(bars, counts):
        if cnt > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                    str(cnt), ha="center", va="bottom", fontsize=10)
    fig.tight_layout()
    return _save(fig, "preventability_histogram.png", output_dir)


# ---------------------------------------------------------------------------
# Summary JSON
# ---------------------------------------------------------------------------

def build_summary(
    root_causes: dict,
    prev_by_diag: dict,
    factors: list[tuple[str, int]],
    interventions: list[tuple[str, int]],
    score_dist: dict[int, int],
    total_reviews: int,
) -> dict:
    return {
        "total_reviews": total_reviews,
        "root_cause_distribution": root_causes,
        "mean_preventability_by_diagnosis": prev_by_diag,
        "top_contributing_factors": {f: c for f, c in factors},
        "top_recommended_interventions": {i: c for i, c in interventions},
        "preventability_score_distribution": {str(k): v for k, v in score_dist.items()},
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def run_analyze(database_url: str | Path | None = None, output_dir: Path | None = None) -> None:
    """Run all analytics, generate visualizations, write summary JSON, print console table."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    conn = get_connection(database_url)
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    reviews = _fetch_reviews(conn)

    if not reviews:
        console.print(
            "[bold yellow]No completed reviews found.[/bold yellow] "
            "Run the 'review' command first to generate readmission reviews."
        )
        conn.close()
        return

    console.print(f"\n[bold]Analyzing {len(reviews)} completed reviews...[/bold]\n")

    # Compute metrics
    root_causes = root_cause_distribution(reviews)
    prev_by_diag = mean_preventability_by_diagnosis(conn, reviews)
    factors = top_contributing_factors(reviews)
    interventions = top_recommended_interventions(reviews)
    score_dist = preventability_score_distribution(reviews)

    # Generate visualizations
    plot_root_cause_distribution(root_causes, out)
    plot_preventability_by_diagnosis(prev_by_diag, out)
    plot_contributing_factors(factors, out)
    plot_recommended_interventions(interventions, out)
    plot_preventability_histogram(score_dist, out)

    console.print(f"[green]Visualizations saved to {out}/[/green]\n")

    # Write summary JSON
    summary = build_summary(root_causes, prev_by_diag, factors, interventions, score_dist, len(reviews))
    summary_path = out / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    console.print(f"[green]Summary JSON written to {summary_path}[/green]\n")

    # Console summary table — root cause distribution
    table = Table(title="Root Cause Distribution")
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Percentage", justify="right")
    for cat, info in root_causes.items():
        table.add_row(cat, str(info["count"]), f"{info['percentage']}%")
    console.print(table)

    # Preventability score distribution table
    score_table = Table(title="\nPreventability Score Distribution")
    score_table.add_column("Score", justify="center")
    score_table.add_column("Count", justify="right")
    for score, cnt in score_dist.items():
        score_table.add_row(str(score), str(cnt))
    console.print(score_table)

    # Top contributing factors
    if factors:
        factor_table = Table(title="\nTop Contributing Factors")
        factor_table.add_column("Factor", style="cyan")
        factor_table.add_column("Frequency", justify="right")
        for f, c in factors[:10]:
            factor_table.add_row(f, str(c))
        console.print(factor_table)

    # Top interventions
    if interventions:
        interv_table = Table(title="\nTop Recommended Interventions")
        interv_table.add_column("Intervention", style="cyan")
        interv_table.add_column("Frequency", justify="right")
        for i, c in interventions[:10]:
            interv_table.add_row(i, str(c))
        console.print(interv_table)

    conn.close()
    console.print("\n[bold green]Analytics complete.[/bold green]")
