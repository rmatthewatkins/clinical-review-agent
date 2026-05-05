"""File-based FHIR source for Synthea Bundle JSON files."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


class SyntheaFileSource:
    """Read FHIR resources from Synthea-generated Bundle JSON files on disk."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def iter_resources(self) -> Iterator[dict]:
        json_files = sorted(self.directory.glob("*.json"))
        if not json_files:
            logger.warning("No .json files found in %s", self.directory)
            return

        for jf in json_files:
            logger.info("Parsing %s", jf.name)
            with open(jf) as f:
                bundle = json.load(f)

            for entry in bundle.get("entry", []):
                resource = entry.get("resource", {})
                if resource.get("resourceType"):
                    yield resource
