"""CLI entrypoint for the readmission review agent."""

import typer

app = typer.Typer(
    name="readmission-agent",
    help="AI-powered clinical readmissions review agent",
)


@app.command()
def ingest(
    source_dir: str = typer.Option(None, "--dir", "-d", help="Path to directory of FHIR Bundle JSON files (e.g., Synthea output)"),
    fhir_url: str = typer.Option(None, "--fhir-url", "-u", help="FHIR R4 server base URL (e.g., http://localhost:8080/fhir)"),
    token: str = typer.Option(None, "--token", "-t", envvar="FHIR_TOKEN", help="Bearer token for FHIR API auth"),
):
    """Ingest FHIR data into SQLite from files or a FHIR server."""
    from src.data.ingest import run_ingest

    if source_dir and fhir_url:
        raise typer.BadParameter("Specify either --dir or --fhir-url, not both.")
    if not source_dir and not fhir_url:
        raise typer.BadParameter("Specify a source: --dir <path> or --fhir-url <url>")

    if source_dir:
        from src.data.sources.synthea_file import SyntheaFileSource
        source = SyntheaFileSource(source_dir)
    else:
        from src.data.sources.fhir_api import FhirApiSource
        token_provider = (lambda: token) if token else None
        source = FhirApiSource(fhir_url, token_provider=token_provider)

    run_ingest(source)


@app.command()
def identify_pairs():
    """Identify 30-day readmission pairs from loaded encounter data."""
    from src.data.pairs import run_identify_pairs
    run_identify_pairs()


@app.command()
def review(
    limit: int = typer.Option(None, "--limit", "-n", help="Max number of pairs to review"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print context for first pair without calling API"),
):
    """Run AI-powered readmission reviews."""
    from src.agent.reviewer import run_review
    run_review(limit=limit, dry_run=dry_run)


@app.command()
def analyze():
    """Generate cohort-level analytics from completed reviews."""
    from src.analytics.analyze import run_analyze
    run_analyze()


if __name__ == "__main__":
    app()
