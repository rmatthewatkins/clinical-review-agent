"""Base class and shared types for AHRQ PSI definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PsiEvaluation:
    """Result of evaluating one PSI against one encounter.

    The engine writes this to ``psi_cases`` as a single row when
    ``numerator_met`` is True (or, optionally, also for denominator-only
    near-misses). The ``poa_*`` fields document how present-on-admission
    was inferred for the numerator condition, since MIMIC-IV does not
    carry POA flags.
    """

    encounter_id: str
    patient_id: str
    numerator_met: bool
    denominator_met: bool
    exclusion_reasons: list[str] = field(default_factory=list)
    poa_imputation: str | None = None        # "prior_admission_lookback" | "assumed_not_poa" | "n/a"
    poa_confidence: str | None = None        # "high" | "medium" | "low"
    # Free-form notes the engine wants to surface to the review agent
    # (e.g. "matched J95.811 in any-listed dx; pleural disease NOT coded").
    notes: list[str] = field(default_factory=list)


@dataclass
class PsiDefinition:
    """A single AHRQ Patient Safety Indicator.

    Subclasses override ``evaluate`` to implement the indicator-specific
    numerator / denominator / exclusion logic. The engine handles common
    plumbing (encounter iteration, age computation, POA lookback).

    Attributes
    ----------
    number : str
        Two-digit AHRQ PSI number, e.g. ``"06"``. Stored as text to
        preserve leading zeros and accommodate composites like ``"90"``.
    name : str
        AHRQ short name, e.g. ``"Iatrogenic Pneumothorax Rate"``.
    description : str
        Clinical summary of what the indicator captures.
    spec_version : str
        AHRQ QI release the code lists were derived from (e.g. ``"v2024"``).
    min_age : int
        Minimum patient age (years) for denominator eligibility.
    """

    number: str
    name: str
    description: str
    spec_version: str = "v2024 (prototype subset)"
    min_age: int = 18

    def evaluate(self, encounter: dict[str, Any], context: dict[str, Any]) -> PsiEvaluation:  # pragma: no cover
        """Evaluate this PSI against a single encounter.

        Parameters
        ----------
        encounter : dict
            The encounters row plus pre-fetched patient demographics.
        context : dict
            Engine-supplied lookups: ``diagnoses`` (list of conditions),
            ``procedures``, ``prior_diagnosis_codes`` (set of codes seen
            in any prior admission for this patient — used for POA
            imputation), ``age``.
        """
        raise NotImplementedError
