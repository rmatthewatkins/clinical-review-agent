"""PSI 03 — Pressure Ulcer Rate.

AHRQ QI v2024 specification (prototype subset).

Numerator
---------
Stage 3, Stage 4, or unstageable pressure ulcer (L89.*3 / L89.*4 / L89.*5)
**not** present on admission.

Denominator
-----------
Adult inpatient discharges (age 18+) with length-of-stay ≥ 3 days.

Exclusions (any of)
-------------------
- LOS < 3 days
- Severe burns (T31, T32 — TBSA codes)
- Exfoliative skin disorders (L00-L08)
- Hemiplegia, paraplegia, quadriplegia (G81, G82, G83.0-G83.4)
- Pressure ulcer is POA (inferred via prior-admission lookback in MIMIC-IV)
- Pregnancy/newborn admission_type
"""

from __future__ import annotations

from datetime import date
from typing import Any

from clinical_review_agent.data.psi_definitions.base import (
    PsiDefinition,
    PsiEvaluation,
)


# ── Code lists (prototype subset) ───────────────────────────────────────

# L89.*3 = stage 3, L89.*4 = stage 4, L89.*5 = unstageable.
# L89.*6 (deep tissue injury) is NOT in the v2024 numerator.
def _is_severe_pressure_ulcer(code: str) -> bool:
    """L89 code with 6th-character stage 3, 4, or 5."""
    c = (code or "").replace(".", "").upper()
    if not c.startswith("L89") or len(c) < 6:
        return False
    return c[5] in {"3", "4", "5"}


SEVERE_BURN_PREFIXES = ("T31", "T32")
EXFOLIATIVE_SKIN_PREFIXES = ("L00", "L01", "L02", "L03", "L04", "L05", "L06", "L07", "L08")
PARALYSIS_PREFIXES = ("G81", "G82")
PARALYSIS_G83_STARTERS = ("G830", "G831", "G832", "G833", "G834")

PREGNANCY_ADMISSION_TYPES = {"NEWBORN"}


def _matches_prefix(code: str | None, prefixes: tuple[str, ...]) -> bool:
    if not code:
        return False
    c = code.replace(".", "").upper()
    return any(c.startswith(p) for p in prefixes)


def _is_paralysis(code: str | None) -> bool:
    if not code:
        return False
    c = code.replace(".", "").upper()
    if any(c.startswith(p) for p in PARALYSIS_PREFIXES):
        return True
    return any(c.startswith(p) for p in PARALYSIS_G83_STARTERS)


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


class Psi03(PsiDefinition):
    def __init__(self) -> None:
        super().__init__(
            number="03",
            name="Pressure Ulcer Rate",
            description=(
                "Cases of stage 3, stage 4, or unstageable pressure ulcer "
                "per 1,000 adult inpatient discharges with LOS ≥ 3 days, "
                "excluding cases where the ulcer was present on admission "
                "or where the patient has comorbid conditions that confer "
                "high baseline risk (severe burns, paralysis, exfoliative "
                "skin disease)."
            ),
            spec_version="AHRQ PSI v2024 (prototype subset)",
            min_age=18,
        )

    def evaluate(self, encounter: dict[str, Any], context: dict[str, Any]) -> PsiEvaluation:
        diagnoses: list[dict] = context["diagnoses"]
        age: int | None = context.get("age")
        prior_codes: set[str] = context.get("prior_diagnosis_codes", set())
        admission_type = (encounter.get("reason_display") or "").upper()

        # Numerator: any severe pressure ulcer code on this encounter
        severe_ulcer_codes = [
            d for d in diagnoses if _is_severe_pressure_ulcer(d.get("code") or "")
        ]
        numerator_hit = bool(severe_ulcer_codes)

        # POA inference: did any L89 (any stage) appear in a prior admission?
        # Any prior pressure-ulcer history makes POA highly likely — the
        # ulcer is rarely truly de-novo on a re-admission.
        poa_imputation = "n/a"
        poa_confidence = "n/a"
        notes: list[str] = []
        if numerator_hit:
            prior_l89 = {
                c for c in prior_codes
                if (c or "").replace(".", "").upper().startswith("L89")
            }
            if prior_l89:
                poa_imputation = "prior_admission_lookback"
                poa_confidence = "high"
                notes.append(
                    f"Pressure ulcer history in prior admissions ({len(prior_l89)} L89 codes) — "
                    "likely POA, not hospital-acquired."
                )
            else:
                poa_imputation = "assumed_not_poa"
                poa_confidence = "medium"
                notes.append(
                    "No prior L89 history for this patient — assumed hospital-"
                    "acquired (MIMIC-IV does not carry POA flags; nursing "
                    "skin-assessment notes should be reviewed to confirm)."
                )

        # Exclusions
        exclusion_reasons: list[str] = []

        if age is not None and age < self.min_age:
            exclusion_reasons.append(f"Age {age} < {self.min_age}")

        los = _length_of_stay(encounter.get("start"), encounter.get("end"))
        if los is not None and los < 3:
            exclusion_reasons.append(f"Length of stay {los} days < 3")

        if admission_type in PREGNANCY_ADMISSION_TYPES:
            exclusion_reasons.append(f"Admission type '{admission_type}' (pregnancy/newborn)")

        for d in diagnoses:
            code = d.get("code")
            if _matches_prefix(code, SEVERE_BURN_PREFIXES):
                exclusion_reasons.append(
                    f"Severe burn coded ({code}: {d.get('display')})"
                )
                break
        for d in diagnoses:
            code = d.get("code")
            if _matches_prefix(code, EXFOLIATIVE_SKIN_PREFIXES):
                exclusion_reasons.append(
                    f"Exfoliative skin disorder ({code}: {d.get('display')})"
                )
                break
        for d in diagnoses:
            code = d.get("code")
            if _is_paralysis(code):
                exclusion_reasons.append(
                    f"Paralysis ({code}: {d.get('display')})"
                )
                break

        if poa_imputation == "prior_admission_lookback":
            exclusion_reasons.append("Ulcer likely POA (prior L89 in patient history)")

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
