"""AHRQ Patient Safety Indicator definitions.

Each PSI is a subclass of ``PsiDefinition`` (see ``base.py``). The registry
in this module is the canonical list the engine iterates over.

Code lists are *prototype subsets* of the AHRQ QI v2024 specifications.
They are not exhaustive — they cover the clinically meaningful codes for
demonstration and are tagged in each definition with the spec version
they're derived from. For production use, swap in the full code lists
distributed with AHRQ QI software.
"""

from clinical_review_agent.data.psi_definitions.base import (
    PsiDefinition,
    PsiEvaluation,
)
from clinical_review_agent.data.psi_definitions.psi_03 import Psi03
from clinical_review_agent.data.psi_definitions.psi_06 import Psi06
from clinical_review_agent.data.psi_definitions.psi_12 import Psi12

REGISTRY: list[PsiDefinition] = [
    Psi06(),
    Psi03(),
    Psi12(),
]

__all__ = ["PsiDefinition", "PsiEvaluation", "REGISTRY"]
