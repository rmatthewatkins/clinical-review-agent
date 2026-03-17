"""FhirSource protocol and shared FHIR parsing helpers."""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable


@runtime_checkable
class FhirSource(Protocol):
    """Interface for FHIR data sources.

    Implementations yield raw FHIR resource dicts (the ``resource`` object
    from bundle entries, or individual resources from API responses).
    """

    def iter_resources(self) -> Iterator[dict]:
        """Yield FHIR resource dicts one at a time."""
        ...


# ---------------------------------------------------------------------------
# Shared FHIR parsing helpers (generic across all sources)
# ---------------------------------------------------------------------------

def ref(reference_str: str | None) -> str | None:
    """Extract the resource ID from a FHIR reference string.

    Handles common formats:
      - ``"Patient/abc"``           → ``"abc"``
      - ``"urn:uuid:abc"``          → ``"abc"``
      - ``"https://server/Patient/abc"`` → ``"abc"``
    """
    if not reference_str:
        return None
    if reference_str.startswith("urn:uuid:"):
        return reference_str[9:]
    if "/" in reference_str:
        return reference_str.split("/")[-1]
    return reference_str


def code_display(codeable_concept: dict | None) -> tuple[str | None, str | None]:
    """Return ``(code, display)`` from a FHIR CodeableConcept."""
    if not codeable_concept:
        return None, None
    codings = codeable_concept.get("coding", [])
    if codings:
        return codings[0].get("code"), codings[0].get("display")
    return None, codeable_concept.get("text")


def extension_value(extensions: list | None, url_fragment: str) -> str | None:
    """Find a FHIR extension by URL fragment and return its display text.

    Works for US Core race/ethnicity extensions (nested ``text`` sub-extension)
    as well as simple ``valueString`` / ``valueCoding`` extensions.
    """
    if not extensions:
        return None
    for ext in extensions:
        if url_fragment in ext.get("url", ""):
            # US Core nests race/ethnicity under a sub-extension with url="text"
            inner = ext.get("extension", [])
            for sub in inner:
                if sub.get("url") == "text":
                    return sub.get("valueString")
            # Fallback: direct valueString / valueCoding
            if "valueString" in ext:
                return ext["valueString"]
            vc = ext.get("valueCoding", {})
            if vc:
                return vc.get("display")
    return None
