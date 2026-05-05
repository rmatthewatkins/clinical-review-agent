"""CLI entrypoint for the clinical review agent."""

import typer

from clinical_review_agent.logging_config import setup_logging

app = typer.Typer(
    name="clinical-review-agent",
    help="AI-powered clinical case review agent (readmissions, mortality)",
)


@app.command()
def ingest(
    source_dir: str = typer.Option(None, "--dir", "-d", help="Path to directory of FHIR Bundle JSON files (e.g., Synthea output)"),
    fhir_url: str = typer.Option(None, "--fhir-url", "-u", help="FHIR R4 server base URL (e.g., http://localhost:8080/fhir)"),
    token: str = typer.Option(None, "--token", "-t", envvar="FHIR_TOKEN", help="Bearer token for FHIR API auth"),
    mimic_bq: bool = typer.Option(False, "--mimic-bq", help="Ingest MIMIC-III from BigQuery (physionet-data.mimiciii_*)"),
    mimic_iv_bq: bool = typer.Option(False, "--mimic-iv-bq", help="Ingest MIMIC-IV from BigQuery (physionet-data.mimiciv_*)"),
    bq_project: str = typer.Option(None, "--bq-project", envvar="BQ_PROJECT_ID", help="GCP project for BigQuery billing (required with --mimic-bq / --mimic-iv-bq)"),
    cohort_size: int = typer.Option(100, "--cohort-size", help="Max patients to ingest from MIMIC (--mimic-bq / --mimic-iv-bq)"),
    cohort_strategy: str = typer.Option("readmit", "--cohort-strategy", help="MIMIC cohort selection: readmit | high-acuity | random"),
    include_labs: bool = typer.Option(True, "--include-labs/--no-include-labs", help="Include lab observations from labevents (--mimic-bq / --mimic-iv-bq)"),
    include_notes: bool = typer.Option(True, "--include-notes/--no-include-notes", help="Include free-text notes (--mimic-bq: noteevents; --mimic-iv-bq: discharge summaries)"),
    include_radiology: bool = typer.Option(False, "--include-radiology/--no-include-radiology", help="Also include radiology reports (--mimic-iv-bq only). Off by default — voluminous."),
    mimic_iv_version: str = typer.Option("3_1", "--mimic-iv-version", help="MIMIC-IV release for the hosp dataset (e.g. '3_1' → physionet-data.mimiciv_3_1_hosp). PhysioNet versions vary per credentialed project."),
    only: str = typer.Option(None, "--only", help="Comma-separated resource types to emit (e.g. 'Note' for selective backfill). Overrides --include-* flags."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Yield resources, log counts/bytes, but do NOT write to PostgreSQL"),
):
    """Ingest FHIR data into PostgreSQL from files, a FHIR server, or MIMIC-III/IV BigQuery."""
    setup_logging(fmt="text")
    from clinical_review_agent.data.ingest import run_ingest

    chosen = sum(bool(x) for x in (source_dir, fhir_url, mimic_bq, mimic_iv_bq))
    if chosen != 1:
        raise typer.BadParameter(
            "Specify exactly one source: --dir <path>, --fhir-url <url>, --mimic-bq, or --mimic-iv-bq."
        )

    if source_dir:
        from clinical_review_agent.data.sources.synthea_file import SyntheaFileSource
        source = SyntheaFileSource(source_dir)
    elif fhir_url:
        from clinical_review_agent.data.sources.fhir_api import FhirApiSource
        token_provider = (lambda: token) if token else None
        source = FhirApiSource(fhir_url, token_provider=token_provider)
    elif mimic_bq:
        from clinical_review_agent.data.sources.mimic_bq import MimicBigQuerySource, ALL_RESOURCE_TYPES
        if not bq_project:
            raise typer.BadParameter("--bq-project (or BQ_PROJECT_ID env var) is required with --mimic-bq.")
        resource_filter = None
        if only:
            requested = {x.strip() for x in only.split(",") if x.strip()}
            unknown = requested - set(ALL_RESOURCE_TYPES)
            if unknown:
                raise typer.BadParameter(
                    f"Unknown resource type(s) in --only: {sorted(unknown)}. "
                    f"Valid: {sorted(ALL_RESOURCE_TYPES)}"
                )
            resource_filter = frozenset(requested)
        source = MimicBigQuerySource(
            project_id=bq_project,
            cohort_size=cohort_size,
            cohort_strategy=cohort_strategy,
            include_labs=include_labs,
            include_notes=include_notes,
            resource_filter=resource_filter,
        )
    else:
        from clinical_review_agent.data.sources.mimic_iv_bq import (
            MimicIvBigQuerySource,
            ALL_RESOURCE_TYPES as MIMIC_IV_TYPES,
        )
        if not bq_project:
            raise typer.BadParameter("--bq-project (or BQ_PROJECT_ID env var) is required with --mimic-iv-bq.")
        resource_filter = None
        if only:
            requested = {x.strip() for x in only.split(",") if x.strip()}
            unknown = requested - set(MIMIC_IV_TYPES)
            if unknown:
                raise typer.BadParameter(
                    f"Unknown resource type(s) in --only: {sorted(unknown)}. "
                    f"Valid: {sorted(MIMIC_IV_TYPES)}"
                )
            resource_filter = frozenset(requested)
        version = mimic_iv_version.strip().replace(".", "_")
        source = MimicIvBigQuerySource(
            project_id=bq_project,
            cohort_size=cohort_size,
            cohort_strategy=cohort_strategy,
            include_labs=include_labs,
            include_notes=include_notes,
            include_radiology=include_radiology,
            resource_filter=resource_filter,
            dataset_hosp=f"physionet-data.mimiciv_{version}_hosp",
        )

    if dry_run:
        _dry_run_count(source)
        return

    run_ingest(source)


def _dry_run_count(source) -> None:
    """Iterate the source and print resourceType counts without DB writes."""
    from collections import Counter
    counts: Counter = Counter()
    sample: dict[str, dict] = {}
    for r in source.iter_resources():
        rt = r.get("resourceType")
        counts[rt] += 1
        if rt and rt not in sample:
            sample[rt] = r
    typer.echo("Dry-run resource counts:")
    for rt, n in sorted(counts.items()):
        typer.echo(f"  {rt:<20} {n:>8}")
    typer.echo("\nFirst sample per resourceType (truncated):")
    import json as _json
    for rt, r in sample.items():
        typer.echo(f"\n--- {rt} ---")
        s = _json.dumps(r, indent=2, default=str)
        typer.echo(s[:1200] + ("…" if len(s) > 1200 else ""))


@app.command()
def identify(
    case_type: str = typer.Option("readmission", "--type", "-t", help="Case type to identify: readmission, mortality, psi"),
):
    """Identify cases (readmissions, mortality, PSIs) from loaded encounter data."""
    setup_logging(fmt="text")
    if case_type == "readmission":
        from clinical_review_agent.data.pairs import run_identify_readmissions
        run_identify_readmissions()
    elif case_type == "mortality":
        from clinical_review_agent.data.mortality import run_identify_mortality
        run_identify_mortality()
    elif case_type == "psi":
        from clinical_review_agent.data.psi import run_identify_psis
        run_identify_psis()
    else:
        raise typer.BadParameter(f"Unknown case type: {case_type}. Use 'readmission', 'mortality', or 'psi'.")


@app.command()
def review(
    limit: int = typer.Option(None, "--limit", "-n", help="Max number of cases to review"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print context for first case without calling API"),
    case_type: str = typer.Option("readmission", "--type", "-t", help="Case type to review: readmission, mortality, psi"),
):
    """Run AI-powered clinical case reviews."""
    from clinical_review_agent.agent.reviewer import run_review
    run_review(limit=limit, dry_run=dry_run, case_type=case_type)


@app.command()
def analyze(
    case_type: str = typer.Option(None, "--type", "-t", help="Filter analytics by case type (default: all)"),
):
    """Generate cohort-level analytics from completed reviews."""
    setup_logging(fmt="text")
    from clinical_review_agent.analytics.analyze import run_analyze
    run_analyze(case_type=case_type)


if __name__ == "__main__":
    app()
