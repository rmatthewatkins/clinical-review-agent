"""Claude-powered clinical case review agent.

Calls the Anthropic API to perform structured peer review of clinical cases
(readmissions, mortality, etc.), stores results in PostgreSQL and output files.
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

from clinical_review_agent.agent.context import assemble_context, context_to_prompt_string
from clinical_review_agent.config import get_settings
from clinical_review_agent.schema import get_connection

logger = logging.getLogger(__name__)
console = Console()

OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "reviews"

# ── System prompts per case type ──────────────────────────────────────

SYSTEM_PROMPTS = {
    "readmission": """\
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

## How the Data is Structured

The case is provided as JSON with sections for `patient_baseline`, `index_admission`, `interval_care`, and `readmission`. Each encounter section contains structured fields (`diagnoses`, `procedures`, `medications`, `observations`) plus, when available:

- **`key_notes`** — full free-text of the most clinically definitive documents for that encounter (discharge summary, admission H&P). When present, **read these first** — they are the primary clinical narrative and typically contain the HPI, hospital course, assessment, and discharge plan you need to evaluate the index admission and recognize the presenting story of the readmission.
- **`notes_index`** — metadata-only list (category, description, date) of all other notes attached to that encounter. Use it to identify whether additional documentation exists (nursing notes, radiology reports, physician progress notes) that you'd want for a more complete review. Cite specific entries from this index when noting documentation that would have changed your assessment.

When `key_notes` is empty for an encounter, the document either was not generated or has not been ingested. Do not invent narrative content; reason only from what is present.

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
""",
    "mortality": """\
You are an experienced, board-certified hospitalist physician performing a structured mortality peer review. You have over 15 years of clinical experience and have served on multiple hospital mortality review committees.

Your task is to review the clinical data for an inpatient encounter that resulted in patient death, then produce a rigorous clinical assessment.

## Your Review Standards

1. **Clinical rigor is paramount.** Base every assessment on the clinical evidence presented. Cite specific diagnoses, lab values, medications, and timeline details when they support your reasoning.

2. **Do NOT hedge.** You are a senior physician rendering a professional opinion. State your assessments directly. If the evidence is insufficient for a definitive judgment, say so plainly and explain what data would be needed.

3. **Apply established clinical frameworks.** Consider:
   - Whether diagnostic workup was timely and appropriate
   - Whether treatment was initiated promptly and was evidence-based
   - Whether escalation of care occurred at appropriate clinical thresholds
   - Whether there were communication failures between teams or with the patient/family
   - Whether the death was expected given the disease trajectory and comorbidity burden
   - Whether systems factors (staffing, equipment, protocols) contributed to the outcome
   - Whether goals of care discussions occurred at appropriate times
   - Whether prior encounters in the lookback window revealed missed opportunities

4. **Distinguish expected from unexpected death.** Not all inpatient deaths represent quality failures. Terminal disease progression, comfort-focused care transitions, and overwhelming acute illness may result in unavoidable deaths despite excellent care.

## How the Data is Structured

The case is provided as JSON with sections for `patient_baseline`, `death_encounter`, and `prior_care` (encounters in the lookback window before this admission). The `death_encounter` section contains structured fields (`diagnoses`, `procedures`, `medications`, `observations`) plus, when available:

- **`key_notes`** — full free-text of the most clinically definitive documents for the death encounter (discharge summary, admission H&P). When present, **read these first** — they contain the hospital course, sequence of clinical decisions, and often documentation of goals-of-care discussions and family meetings. The discharge summary for a death encounter is effectively the hospital-course-to-death narrative.
- **`notes_index`** — metadata-only list (category, description, date) of all other notes attached to the encounter (nursing, radiology, physician progress notes, social work, case management). Use it to identify whether documentation exists that you'd want to read for a fuller assessment, and to comment on whether a goals-of-care or palliative-care note appears in the trajectory.

When `key_notes` is empty, the document either was not generated or has not been ingested. Do not invent narrative content; reason only from what is present.

## Required Output

You MUST produce exactly TWO outputs in your response, clearly separated:

### Output 1: Structured Assessment (JSON)

Wrap this in ```json``` code fences. The JSON object must have these exact fields:

- **root_cause_category**: One of: "diagnostic_error", "treatment_delay", "medication_error", "system_failure", "disease_progression", "comorbidity_burden", "communication_failure", "unavoidable", "other"
- **preventability_score**: Integer 1-5 where:
  - 1 = Clearly not preventable — death was due to factors entirely outside the health system's control
  - 2 = Likely not preventable — reasonable care was provided; death driven primarily by disease severity
  - 3 = Possibly preventable — some deficiencies identified but unclear if correcting them would have changed the outcome
  - 4 = Likely preventable — identifiable gaps in care that probably contributed to the death
  - 5 = Clearly preventable — obvious failures in care that directly led to the death
- **preventability_rationale**: 2-4 sentences explaining your preventability score, referencing specific clinical evidence
- **contributing_factors**: Array of strings, each a concise contributing factor
- **recommended_interventions**: Array of strings, each a specific, actionable intervention
- **confidence_level**: "high", "moderate", or "low" — your confidence in the assessment given the available data

### Output 2: Clinical Narrative

After the JSON block, write a 2-3 paragraph clinical narrative. This should read like a physician's mortality review note — professional prose, not bullet points. It should:
- Summarize the clinical trajectory leading to death
- Identify the key clinical decision points where the outcome may have been altered
- Provide your overall assessment in the tone a hospitalist would use when presenting at a mortality and morbidity conference

Do NOT use headers, bullet points, or lists in the narrative. Write in flowing clinical prose.
""",
}

USER_PROMPT_PREFIXES = {
    "readmission": "Please review the following 30-day hospital readmission case.",
    "mortality": "Please review the following inpatient mortality case.",
}

# Keep SYSTEM_PROMPT as alias for backwards compatibility
SYSTEM_PROMPT = SYSTEM_PROMPTS["readmission"]


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


def _call_claude(prompt: str, case_type: str = "readmission") -> tuple[str, int]:
    """Call the Anthropic API with exponential backoff retry.

    Returns
    -------
    (response_text, total_tokens_used)
    """
    cfg = get_settings()
    client = anthropic.Anthropic()
    max_retries = cfg.claude_max_retries
    base_delay = cfg.claude_retry_base_delay
    max_delay = cfg.claude_retry_max_delay

    system_prompt = SYSTEM_PROMPTS.get(case_type, SYSTEM_PROMPTS["readmission"])

    for attempt in range(max_retries + 1):
        try:
            message = client.messages.create(
                model=cfg.claude_model,
                max_tokens=cfg.claude_max_tokens,
                system=system_prompt,
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
    case_id: int,
    structured: dict[str, Any],
    narrative: str,
    tokens_used: int,
    case_type: str = "readmission",
) -> None:
    """Store the review in the database and write output files."""
    cfg = get_settings()
    model = cfg.claude_model
    now = datetime.now(timezone.utc).isoformat()
    structured_json_str = json.dumps(structured, indent=2)

    conn.execute(
        """INSERT INTO reviews (case_type, case_id, structured_json, clinical_narrative, model_used, created_at, tokens_used)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (case_type, case_id) DO UPDATE SET
               structured_json = EXCLUDED.structured_json,
               clinical_narrative = EXCLUDED.clinical_narrative,
               model_used = EXCLUDED.model_used,
               created_at = EXCLUDED.created_at,
               tokens_used = EXCLUDED.tokens_used""",
        (case_type, case_id, structured_json_str, narrative, model, now, tokens_used),
    )
    conn.commit()

    # Write output files
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_path = OUTPUT_DIR / f"{case_type}_{case_id}.json"
    json_output = {
        "case_type": case_type,
        "case_id": case_id,
        "model_used": model,
        "created_at": now,
        "tokens_used": tokens_used,
        "structured_review": structured,
        "clinical_narrative": narrative,
    }
    json_path.write_text(json.dumps(json_output, indent=2))

    md_path = OUTPUT_DIR / f"{case_type}_{case_id}.md"
    title = "Readmission" if case_type == "readmission" else case_type.replace("_", " ").title()
    md_content = f"""# {title} Review #{case_id}

**Model:** {model}
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


# ── Case table queries per type ───────────────────────────────────────

_CASE_QUERIES = {
    "readmission": {
        "unreviewed": """
            SELECT rp.*
            FROM readmissions rp
            LEFT JOIN reviews r ON r.case_type = 'readmission' AND r.case_id = rp.id
            WHERE r.id IS NULL
            ORDER BY rp.id
        """,
        "label": "readmission",
        "id_field": "id",
    },
    "mortality": {
        "unreviewed": """
            SELECT mc.*
            FROM mortality_cases mc
            LEFT JOIN reviews r ON r.case_type = 'mortality' AND r.case_id = mc.id
            WHERE r.id IS NULL
            ORDER BY mc.id
        """,
        "label": "mortality case",
        "id_field": "id",
    },
}


def run_review(
    limit: int | None = None,
    dry_run: bool = False,
    case_type: str = "readmission",
) -> None:
    """Run AI-powered clinical case reviews.

    Parameters
    ----------
    limit : int, optional
        Maximum number of cases to review. None means review all.
    dry_run : bool
        If True, assemble and print the context for the first unreviewed
        case, then exit without calling the API.
    case_type : str
        Type of case to review: "readmission" or "mortality".
    """
    from clinical_review_agent.logging_config import setup_logging
    cfg = get_settings()
    setup_logging(level=cfg.log_level, fmt="text")

    conn = get_connection()
    cur = conn.cursor()

    case_info = _CASE_QUERIES.get(case_type)
    if not case_info:
        console.print(f"[red]Unknown case type: {case_type}[/red]")
        return

    # Find cases without reviews
    cur.execute(case_info["unreviewed"])
    unreviewed = cur.fetchall()

    label = case_info["label"]

    if not unreviewed:
        console.print(f"[bold green]All {label}s have been reviewed.[/bold green]")
        return

    total = len(unreviewed)
    if limit:
        unreviewed = unreviewed[:limit]

    console.print(f"[bold]Found {total} unreviewed {label}(s). Will review {len(unreviewed)}.[/bold]\n")

    if dry_run:
        row = unreviewed[0]
        context = assemble_context(case_type, row, conn)
        prompt_str = context_to_prompt_string(context)

        case_id = dict(row)[case_info["id_field"]]
        console.print(Panel(
            f"[bold]{label.title()} ID:[/bold] {case_id}\n"
            f"[bold]Patient:[/bold] {dict(row)['patient_id']}",
            title=f"Dry Run — First Unreviewed {label.title()}",
        ))
        console.print("\n[bold]Assembled Clinical Context:[/bold]\n")
        console.print(Syntax(prompt_str, "json", theme="monokai", line_numbers=False))
        console.print(f"\n[dim]Context length: {len(prompt_str):,} characters[/dim]")
        return

    user_prefix = USER_PROMPT_PREFIXES.get(case_type, USER_PROMPT_PREFIXES["readmission"])

    for idx, row in enumerate(unreviewed, 1):
        case_id = dict(row)[case_info["id_field"]]
        console.print(f"Reviewing {label} {case_id} ({idx}/{len(unreviewed)})...")

        try:
            context = assemble_context(case_type, row, conn)
            prompt_str = context_to_prompt_string(context)

            user_prompt = (
                f"{user_prefix} "
                "The clinical data is provided as structured JSON.\n\n"
                f"{prompt_str}"
            )

            response_text, tokens_used = _call_claude(user_prompt, case_type=case_type)
            structured, narrative = _parse_response(response_text)
            _store_review(conn, case_id, structured, narrative, tokens_used, case_type=case_type)

            console.print(
                f"  [green]Done[/green] — "
                f"root cause: {structured.get('root_cause_category', 'N/A')}, "
                f"preventability: {structured.get('preventability_score', 'N/A')}/5, "
                f"tokens: {tokens_used:,}"
            )
        except Exception:
            logger.exception(f"Failed to review {label} {case_id}")
            console.print(f"  [red]Error reviewing {label} {case_id} — see logs[/red]")

    console.print(f"\n[bold green]Completed {len(unreviewed)} review(s).[/bold green]")
    conn.close()
