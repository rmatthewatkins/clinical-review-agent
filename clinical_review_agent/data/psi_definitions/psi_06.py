"""PSI 06 — Iatrogenic Pneumothorax Rate.

AHRQ QI v2024 specification (prototype subset).

Numerator
---------
Postprocedural pneumothorax (J95.811) coded on the discharge.

Denominator
-----------
All medical and surgical inpatient discharges age 18+.

Exclusions (any of)
-------------------
- Pleural disease coded (J90, J91.*, J94.*) — drainage of pleural effusion
  via thoracentesis is a known cause of pneumothorax that the AHRQ spec
  removes from the indicator. **This is the canonical "documentation gap"
  case the review agent should flag**: if a radiology report describes a
  pleural effusion but no J90/J91/J94 code is present, the case looks
  like a PSI hit but actually shouldn't be.
- Chest trauma codes (S20-S29 selected ranges)
- Procedure codes that imply pleural-cavity surgery and therefore an
  expected pneumothorax risk: thoracic biopsy/excision, pleural
  procedures, diaphragmatic surgery
- Patient age < 18
- Pregnancy/childbirth (admission_type heuristic — MIMIC-IV doesn't
  expose MDC; we approximate with admission_type = "NEWBORN" exclusion)
"""

from __future__ import annotations

from typing import Any

from clinical_review_agent.data.psi_definitions.base import (
    PsiDefinition,
    PsiEvaluation,
)


# ── Code lists (prototype subset) ───────────────────────────────────────

NUMERATOR_CODES = {"J95.811", "J95811"}  # MIMIC-IV strips dots in some loads

# Pleural disease: J90 (pleural effusion NOS), J91.* (pleural effusion in
# conditions classified elsewhere), J94.* (other disorders of pleura
# including hemothorax, fibrothorax, pleural plaque, chylous effusion).
# AHRQ excludes any of these to remove cases where the pneumothorax is
# expected as a consequence of the underlying pleural pathology or its
# drainage.
PLEURAL_DISEASE_PREFIXES = ("J90", "J91", "J94")

# Chest trauma exclusions — selected S2x ranges per AHRQ v2024.
CHEST_TRAUMA_PREFIXES = (
    "S20",  # Superficial injury of thorax
    "S21",  # Open wound of thorax
    "S22",  # Fracture of rib(s), sternum and thoracic spine
    "S23",  # Dislocation/sprain of joints/ligaments of thorax
    "S24",  # Injury of nerves and spinal cord at thorax level
    "S25",  # Injury of blood vessels of thorax
    "S26",  # Injury of heart
    "S27",  # Injury of other and unspecified intrathoracic organs
    "S28",  # Crushing injury of thorax / traumatic amputation
    "S29",  # Other and unspecified injuries of thorax
)

# ICD-10-PCS prefixes for procedures that imply pleural-cavity entry
# and therefore exclude the case (pneumothorax expected).
THORACIC_PROCEDURE_PREFIXES = (
    "0BB",   # Excision of respiratory system (lung biopsy, lobectomy)
    "0BT",   # Resection of respiratory system (pneumonectomy)
    "0B9",   # Drainage of respiratory system (chest tube, thoracentesis)
    "0BC",   # Extirpation respiratory system
    "0BJ",   # Inspection of respiratory system (thoracoscopy)
    "0BN",   # Release of respiratory system
    "02Q",   # Repair of cardiac structures (open heart surgery)
    "02R",   # Replacement cardiac valve
    "02H",   # Insertion cardiac device
    "0WB",   # Excision of anatomical regions, general (chest wall)
    "0WJ",   # Inspection of body cavities (thoracic)
)

PREGNANCY_ADMISSION_TYPES = {"NEWBORN"}


def _matches_prefix(code: str | None, prefixes: tuple[str, ...]) -> bool:
    if not code:
        return False
    c = code.replace(".", "").upper()
    return any(c.startswith(p) for p in prefixes)


# ── Definition ──────────────────────────────────────────────────────────


class Psi06(PsiDefinition):
    def __init__(self) -> None:
        super().__init__(
            number="06",
            name="Iatrogenic Pneumothorax Rate",
            description=(
                "Cases of postprocedural pneumothorax (J95.811) per "
                "1,000 adult medical/surgical discharges, excluding "
                "pleural-disease, chest-trauma, and thoracic-surgery "
                "encounters where pneumothorax is an expected risk."
            ),
            spec_version="AHRQ PSI v2024 (prototype subset)",
            min_age=18,
        )

    def evaluate(self, encounter: dict[str, Any], context: dict[str, Any]) -> PsiEvaluation:
        diagnoses: list[dict] = context["diagnoses"]
        procedures: list[dict] = context["procedures"]
        age: int | None = context.get("age")
        prior_codes: set[str] = context.get("prior_diagnosis_codes", set())

        diag_codes = {(d.get("code") or "").replace(".", "").upper() for d in diagnoses if d.get("code")}
        proc_codes = {(p.get("code") or "").replace(".", "").upper() for p in procedures if p.get("code")}
        admission_type = (encounter.get("reason_display") or "").upper()

        # Numerator: J95.811 on discharge (any-listed)
        numerator_codes_norm = {c.replace(".", "").upper() for c in NUMERATOR_CODES}
        numerator_hit = bool(diag_codes & numerator_codes_norm)

        # POA inference for the numerator code: did the same condition
        # appear in any prior admission for this patient?
        poa_imputation = "n/a"
        poa_confidence = "n/a"
        notes: list[str] = []
        if numerator_hit:
            prior_norm = {c.replace(".", "").upper() for c in prior_codes}
            if prior_norm & numerator_codes_norm:
                poa_imputation = "prior_admission_lookback"
                poa_confidence = "high"
                notes.append(
                    "J95.811 appeared on a prior admission for this patient — "
                    "likely present-on-admission, not a hospital-acquired event."
                )
            else:
                poa_imputation = "assumed_not_poa"
                poa_confidence = "medium"
                notes.append(
                    "J95.811 did not appear on any prior admission — assumed "
                    "hospital-acquired (POA flag absent in MIMIC-IV)."
                )

        # Denominator exclusions
        exclusion_reasons: list[str] = []

        if age is not None and age < self.min_age:
            exclusion_reasons.append(f"Age {age} < {self.min_age}")
        if admission_type in PREGNANCY_ADMISSION_TYPES:
            exclusion_reasons.append(f"Admission type '{admission_type}' (pregnancy/newborn)")

        for d in diagnoses:
            code = (d.get("code") or "").replace(".", "").upper()
            if _matches_prefix(code, PLEURAL_DISEASE_PREFIXES):
                exclusion_reasons.append(
                    f"Pleural disease coded ({d.get('code')}: {d.get('display')})"
                )
                break
        for d in diagnoses:
            code = (d.get("code") or "").replace(".", "").upper()
            if _matches_prefix(code, CHEST_TRAUMA_PREFIXES):
                exclusion_reasons.append(
                    f"Chest trauma coded ({d.get('code')}: {d.get('display')})"
                )
                break
        for p in procedures:
            code = (p.get("code") or "").replace(".", "").upper()
            if _matches_prefix(code, THORACIC_PROCEDURE_PREFIXES):
                exclusion_reasons.append(
                    f"Thoracic/cardiac procedure ({p.get('code')}: {p.get('display')})"
                )
                break

        # Also flag POA-likely cases as excluded from the numerator —
        # AHRQ explicitly removes POA pneumothorax from the count.
        if poa_imputation == "prior_admission_lookback":
            exclusion_reasons.append("Pneumothorax likely POA (prior admission match)")

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
