"""PSI 12 — Perioperative Pulmonary Embolism or Deep Vein Thrombosis Rate.

AHRQ QI v2024 specification (prototype subset).

Numerator
---------
Deep vein thrombosis (I82.*) or pulmonary embolism (I26.*) coded on
the discharge and **not** present on admission.

Denominator
-----------
Adult surgical discharges (age 18+) with at least one operating-room
procedure on the encounter.

Exclusions (any of)
-------------------
- VTE is POA (inferred via prior-admission lookback in MIMIC-IV)
- Principal diagnosis of VTE (i.e. VTE was the reason for admission)
- LOS < 2 days (insufficient time for hospital-acquired VTE)
- No procedure on the encounter (denominator inclusion fails)
- Pregnancy/newborn admission_type

Note on temporal ordering
-------------------------
The full AHRQ spec requires the VTE code to be timestamped *after* the
first OR procedure. MIMIC-IV's procedure dates are typically the
admission date (not the actual procedure timestamp), so we approximate
with "any procedure on the encounter + VTE coded + not POA". Cases
where the VTE clearly preceded the procedure should be flagged by the
review agent reading the discharge summary.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from clinical_review_agent.data.psi_definitions.base import (
    PsiDefinition,
    PsiEvaluation,
)


# ── Code lists (prototype subset) ───────────────────────────────────────

DVT_PREFIXES = ("I82",)
PE_PREFIXES = ("I26",)
VTE_PREFIXES = DVT_PREFIXES + PE_PREFIXES

# ICD-10-PCS root operations that AHRQ counts as "operating room
# procedures." Prototype subset — full spec lists hundreds of codes.
OR_PROCEDURE_PREFIXES = (
    "0",   # All "Medical and Surgical" procedures (broad)
    # The leading "0" + non-trivial body system catches most surgical
    # PCS codes. We further filter below with a deny-list of root
    # operations that are not OR procedures (e.g. Drainage of skin).
)

# Roots we DON'T count as OR procedures even though they start with "0":
NON_OR_ROOTS = {
    "0H9",   # Drainage of skin and breast
    "0HJ",   # Inspection of skin
    "0J9",   # Drainage of subcutaneous tissue
    "0JJ",   # Inspection of subcutaneous tissue
    "0W9",   # Drainage of anatomical regions, general
    "0WJ",   # Inspection of body cavities (only diagnostic)
    "0BJ",   # Inspection respiratory system (diagnostic)
}

PREGNANCY_ADMISSION_TYPES = {"NEWBORN"}


def _matches_prefix(code: str | None, prefixes: tuple[str, ...]) -> bool:
    if not code:
        return False
    c = code.replace(".", "").upper()
    return any(c.startswith(p) for p in prefixes)


def _is_or_procedure(code: str | None) -> bool:
    if not code:
        return False
    c = code.replace(".", "").upper()
    if not c.startswith("0"):
        return False
    if any(c.startswith(root) for root in NON_OR_ROOTS):
        return False
    # PCS codes are 7 chars; anything shorter is likely an ICD-9-PCS
    # numeric code which we conservatively count as an OR procedure.
    return True


def _length_of_stay(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        d1 = date.fromisoformat(start[:10])
        d2 = date.fromisoformat(end[:10])
        return (d2 - d1).days
    except (ValueError, TypeError):
        return None


# ── Definition ──────────────────────────────────────────────────────────


class Psi12(PsiDefinition):
    def __init__(self) -> None:
        super().__init__(
            number="12",
            name="Perioperative Pulmonary Embolism or Deep Vein Thrombosis Rate",
            description=(
                "Cases of DVT (I82) or PE (I26) per 1,000 adult surgical "
                "discharges, where the VTE was not present on admission "
                "and the patient had at least one operating-room "
                "procedure on the encounter."
            ),
            spec_version="AHRQ PSI v2024 (prototype subset)",
            min_age=18,
        )

    def evaluate(self, encounter: dict[str, Any], context: dict[str, Any]) -> PsiEvaluation:
        diagnoses: list[dict] = context["diagnoses"]
        procedures: list[dict] = context["procedures"]
        age: int | None = context.get("age")
        prior_codes: set[str] = context.get("prior_diagnosis_codes", set())
        admission_type = (encounter.get("reason_display") or "").upper()

        # Numerator: any VTE code on this encounter
        vte_hits = [
            d for d in diagnoses if _matches_prefix(d.get("code"), VTE_PREFIXES)
        ]
        numerator_hit = bool(vte_hits)

        # POA inference for VTE
        poa_imputation = "n/a"
        poa_confidence = "n/a"
        notes: list[str] = []
        if numerator_hit:
            prior_vte = {
                c for c in prior_codes
                if _matches_prefix(c, VTE_PREFIXES)
            }
            if prior_vte:
                poa_imputation = "prior_admission_lookback"
                poa_confidence = "high"
                notes.append(
                    f"Prior VTE history ({len(prior_vte)} I26/I82 codes in past admissions) — "
                    "likely POA or recurrent rather than hospital-acquired."
                )
            else:
                poa_imputation = "assumed_not_poa"
                poa_confidence = "medium"
                notes.append(
                    "No prior VTE history — assumed hospital-acquired. Review "
                    "discharge summary for evidence the VTE was diagnosed at "
                    "or before admission (POA flag absent in MIMIC-IV)."
                )

        # Exclusions
        exclusion_reasons: list[str] = []

        if age is not None and age < self.min_age:
            exclusion_reasons.append(f"Age {age} < {self.min_age}")

        los = _length_of_stay(encounter.get("start"), encounter.get("end"))
        if los is not None and los < 2:
            exclusion_reasons.append(f"Length of stay {los} days < 2")

        if admission_type in PREGNANCY_ADMISSION_TYPES:
            exclusion_reasons.append(f"Admission type '{admission_type}' (pregnancy/newborn)")

        # Denominator inclusion: at least one OR procedure
        or_procs = [p for p in procedures if _is_or_procedure(p.get("code"))]
        if not or_procs:
            exclusion_reasons.append("No operating-room procedure on encounter")

        # Principal-diagnosis-of-VTE check: in MIMIC-IV the principal
        # diagnosis is approximated by the encounter's reason_code.
        principal_code = (encounter.get("reason_code") or "").replace(".", "").upper()
        if _matches_prefix(principal_code, VTE_PREFIXES):
            exclusion_reasons.append(
                f"Principal diagnosis is VTE ({encounter.get('reason_code')}) — "
                "VTE was the admit reason, not a perioperative complication"
            )

        if poa_imputation == "prior_admission_lookback":
            exclusion_reasons.append("VTE likely POA (prior I26/I82 in patient history)")

        denominator_met = numerator_hit and not exclusion_reasons

        return PsiEvaluation(
            encounter_id=encounter["id"],
            patient_id=encounter["patient_id"],
            numerator_met=numerator_hit,
            denominator_met=denominator_met,
            exclusion_reasons=exclusion_reasons,
            poa_imputation=poa_imputation,
            poa_confidence=poa_confidence,
            notes=notes,
        )
