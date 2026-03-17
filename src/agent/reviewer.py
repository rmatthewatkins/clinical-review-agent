"""Claude-powered readmission review agent.

Calls the Anthropic API to perform structured peer review of 30-day
hospital readmissions, stores results in PostgreSQL and output files.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from src.agent.context import assemble_context, context_to_prompt_string
from src.schema import get_connection

logger = logging.getLogger(__name__)
console = Console()

MODEL = "claude-sonnet-4-6"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "reviews"

SYSTEM_PROMPT = """\
You are an experienced, board-certified hospitalist physician performing a structured peer review of a 30-day hospital readmission. You have over 15 years of clinical experience and have served on multiple hospital readmission reduction committees.

Your task is to review the clinical data for an index hospital admission and a subsequent readmission that occurred within 30 days, then produce a rigorous clinical assessment.

## Your Review Standards

1. **Clinical rigor is paramount.** Base every assessment on the clinical evidence presented. Cite specific diagnoses, lab values, medications, and timeline details when they support your reasoning.

2. **Do NOT hedge.** You are a senior physician rendering a professional opinion. State your assessments directly. Do not say "it is possible that" or "one might consider" — say "the readmission was caused by" or "the discharge plan failed to address." If the evidence is insufficient for a definitive judgment, say so plainly and explain what data would be needed.

3. **Apply established clinical frameworks.** Consider:
   - Whether the index admission length of stay was adequate for the presenting condition
   - Whether the discharge plan addressed all active clinical problems
   - Whether medication reconciliation was adequate (look for polypharmacy, high-risk medications, missing medications)
   - Whether appropriate follow-up was arranged given the patient's risk profile
   - Whether social determinants (age, location, access to care) were adequately considered
   - Whether the readmission diagnosis is clinically related to the index admission
   - Whether interval care (or lack thereof) contributed to the readmission

4. **Distinguish correlation from causation.** A readmission within 30 days does not automatically mean the index discharge was deficient. Some readmissions are genuinely unavoidable due to disease progression, new unrelated events, or patient factors beyond the health system's control.

## Required Output

You MUST produce exactly TWO outputs in your response, clearly separated:

### Output 1: Structured Assessment (JSON)

Wrap this in ```json``` code fences. The JSON object must have these exact fields:

- **root_cause_category**: One of: "premature_discharge", "inadequate_transition_planning", "medication_related", "inadequate_follow_up", "disease_progression", "social_determinants", "patient_behavioral", "unavoidable", "other"
- **preventability_score**: Integer 1-5 where:
  - 1 = Clearly not preventable — readmission was due to factors entirely outside the health system's control
  - 2 = Likely not preventable — reasonable care was provided; readmission driven primarily by disease severity or patient factors
  - 3 = Possibly preventable — some deficiencies identified but unclear if correcting them would have changed the outcome
  - 4 = Likely preventable — identifiable gaps in care that probably contributed to the readmission
  - 5 = Clearly preventable — obvious failures in care that directly led to the readmission
- **preventability_rationale**: 2-4 sentences explaining your preventability score, referencing specific clinical evidence
- **contributing_factors**: Array of strings, each a concise contributing factor (e.g., "No follow-up appointment scheduled within 7 days of discharge", "High-risk medication started without adequate monitoring plan")
- **recommended_interventions**: Array of strings, each a specific, actionable intervention that could have prevented this readmission or should be implemented for future similar cases
- **confidence_level**: "high", "moderate", or "low" — your confidence in the assessment given the available data

### Output 2: Clinical Narrative

After the JSON block, write a 2-3 paragraph clinical narrative. This should read like a physician's peer review note — professional prose, not bullet points. It should:
- Summarize the clinical trajectory from index admission through readmission
- Identify the key clinical decision points where the outcome may have been altered
- Provide your overall assessment in the tone a hospitalist would use when presenting at a morbidity and mortality conference

Do NOT use headers, bullet points, or lists in the narrative. Write in flowing clinical prose.
"""


def _parse_response(text: str) -> tuple[dict[str, Any], str]:
    """Parse the Claude response into structured JSON and clinical narrative.

    Returns
    -------
    (structured_dict, narrative_text)
    """
    # Extract JSON from ```json ... ``` fences
    json_match = re.search(r"```json\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
        structured = json.loads(json_str)
    else:
        # Fallback: try to find any JSON object in the text
        brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if brace_match:
            structured = json.loads(brace_match.group(0))
        else:
            raise ValueError("Could not extract structured JSON from model response")

    # Extract narrative: everything after the closing ``` of the JSON block
    if json_match:
        narrative = text[json_match.end():].strip()
    else:
        # If we used the fallback, take everything after the JSON object
        narrative = text[text.index("}") + 1:].strip() if "}" in text else ""

    # Clean up any leading markdown headers from the narrative
    narrative = re.sub(r"^#+\s*.*?\n", "", narrative).strip()

    return structured, narrative


def _call_claude(prompt: str) -> tuple[str, int]:
    """Call the Anthropic API with exponential backoff retry.

    Returns
    -------
    (response_text, total_tokens_used)
    """
    client = anthropic.Anthropic()
    max_retries = 5
    base_delay = 1.0
    max_delay = 60.0

    for attempt in range(max_retries + 1):
        try:
            message = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = message.content[0].text
            tokens = message.usage.input_tokens + message.usage.output_tokens
            return text, tokens
        except anthropic.RateLimitError:
            if attempt == max_retries:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(f"Rate limited, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(delay)
        except anthropic.APIStatusError as e:
            if e.status_code == 429:
                if attempt == max_retries:
                    raise
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(f"Rate limited (429), retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                raise

    # Should not reach here, but just in case
    raise RuntimeError("Exhausted all retries")


def _store_review(
    conn,
    pair_id: int,
    structured: dict[str, Any],
    narrative: str,
    tokens_used: int,
) -> None:
    """Store the review in the database and write output files."""
    now = datetime.now(timezone.utc).isoformat()
    structured_json_str = json.dumps(structured, indent=2)

    conn.execute(
        """INSERT INTO reviews (pair_id, structured_json, clinical_narrative, model_used, created_at, tokens_used)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (pair_id) DO UPDATE SET
               structured_json = EXCLUDED.structured_json,
               clinical_narrative = EXCLUDED.clinical_narrative,
               model_used = EXCLUDED.model_used,
               created_at = EXCLUDED.created_at,
               tokens_used = EXCLUDED.tokens_used""",
        (pair_id, structured_json_str, narrative, MODEL, now, tokens_used),
    )
    conn.commit()

    # Write output files
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_path = OUTPUT_DIR / f"pair_{pair_id}.json"
    json_output = {
        "pair_id": pair_id,
        "model_used": MODEL,
        "created_at": now,
        "tokens_used": tokens_used,
        "structured_review": structured,
        "clinical_narrative": narrative,
    }
    json_path.write_text(json.dumps(json_output, indent=2))

    md_path = OUTPUT_DIR / f"pair_{pair_id}.md"
    md_content = f"""# Readmission Review — Pair {pair_id}

**Model:** {MODEL}
**Date:** {now}
**Tokens Used:** {tokens_used}

## Structured Assessment

```json
{structured_json_str}
```

## Clinical Narrative

{narrative}
"""
    md_path.write_text(md_content)


def run_review(limit: int | None = None, dry_run: bool = False) -> None:
    """Run AI-powered readmission reviews.

    Parameters
    ----------
    limit : int, optional
        Maximum number of pairs to review. None means review all.
    dry_run : bool
        If True, assemble and print the context for the first unreviewed
        pair, then exit without calling the API.
    """
    conn = get_connection()
    cur = conn.cursor()

    # Find pairs without reviews
    cur.execute(
        """SELECT rp.*
           FROM readmission_pairs rp
           LEFT JOIN reviews r ON r.pair_id = rp.id
           WHERE r.id IS NULL
           ORDER BY rp.id"""
    )
    unreviewed = cur.fetchall()

    if not unreviewed:
        console.print("[bold green]All readmission pairs have been reviewed.[/bold green]")
        return

    total = len(unreviewed)
    if limit:
        unreviewed = unreviewed[:limit]

    console.print(f"[bold]Found {total} unreviewed pair(s). Will review {len(unreviewed)}.[/bold]\n")

    if dry_run:
        pair = unreviewed[0]
        context = assemble_context(pair, conn)
        prompt_str = context_to_prompt_string(context)

        console.print(Panel(
            f"[bold]Pair ID:[/bold] {dict(pair)['id']}\n"
            f"[bold]Patient:[/bold] {dict(pair)['patient_id']}\n"
            f"[bold]Days between:[/bold] {dict(pair)['days_between']}",
            title="Dry Run — First Unreviewed Pair",
        ))
        console.print("\n[bold]Assembled Clinical Context:[/bold]\n")
        console.print(Syntax(prompt_str, "json", theme="monokai", line_numbers=False))
        console.print(f"\n[dim]Context length: {len(prompt_str):,} characters[/dim]")
        return

    for idx, pair in enumerate(unreviewed, 1):
        pair_dict = dict(pair)
        pair_id = pair_dict["id"]
        console.print(f"Reviewing pair {pair_id} ({idx}/{len(unreviewed)})...")

        try:
            context = assemble_context(pair, conn)
            prompt_str = context_to_prompt_string(context)

            user_prompt = (
                "Please review the following 30-day hospital readmission case. "
                "The clinical data is provided as structured JSON.\n\n"
                f"{prompt_str}"
            )

            response_text, tokens_used = _call_claude(user_prompt)
            structured, narrative = _parse_response(response_text)
            _store_review(conn, pair_id, structured, narrative, tokens_used)

            console.print(
                f"  [green]Done[/green] — "
                f"root cause: {structured.get('root_cause_category', 'N/A')}, "
                f"preventability: {structured.get('preventability_score', 'N/A')}/5, "
                f"tokens: {tokens_used:,}"
            )
        except Exception:
            logger.exception(f"Failed to review pair {pair_id}")
            console.print(f"  [red]Error reviewing pair {pair_id} — see logs[/red]")

    console.print(f"\n[bold green]Completed {len(unreviewed)} review(s).[/bold green]")
    conn.close()
