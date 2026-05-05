"""FHIR R4 REST API client implementing the FhirSource protocol."""

from __future__ import annotations

import logging
import time
from typing import Callable, Iterator

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_RESOURCE_TYPES = [
    "Patient",
    "Encounter",
    "Condition",
    "MedicationRequest",
    "Observation",
    "Procedure",
    "CarePlan",
]

_PATIENT_ID_BATCH_SIZE = 50
_PAGE_SIZE = 100
_MAX_RETRIES = 5
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 60.0
_TIMEOUT = 30.0


class FhirApiSource:
    """Fetch FHIR R4 resources from a REST API (Epic, HAPI, etc.).

    Implements the :class:`~data.sources.base.FhirSource` protocol by
    iterating over search result Bundles with automatic pagination, retry
    with exponential backoff, and optional Bearer-token authentication.
    """

    def __init__(
        self,
        base_url: str,
        token_provider: Callable[[], str] | None = None,
        resource_types: list[str] | None = None,
        patient_ids: list[str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_provider = token_provider
        self.resource_types = resource_types or list(_DEFAULT_RESOURCE_TYPES)
        self.patient_ids = patient_ids

    # ------------------------------------------------------------------
    # FhirSource protocol
    # ------------------------------------------------------------------

    def iter_resources(self) -> Iterator[dict]:
        """Yield FHIR resource dicts across all configured resource types."""
        with httpx.Client(timeout=_TIMEOUT) as client:
            for rtype in self.resource_types:
                logger.info("Fetching %s resources …", rtype)
                try:
                    yield from self._iter_resource_type(client, rtype)
                except _AuthError as exc:
                    raise RuntimeError(
                        f"Authentication failed fetching {rtype}: {exc}"
                    ) from exc
                except Exception:
                    logger.exception("Failed to fetch %s — skipping", rtype)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _iter_resource_type(
        self, client: httpx.Client, resource_type: str
    ) -> Iterator[dict]:
        """Yield resources for a single type, respecting patient scoping."""
        if self.patient_ids and resource_type != "Patient":
            # Batch patient IDs to avoid overly-long URLs.
            for batch in _batched(self.patient_ids, _PATIENT_ID_BATCH_SIZE):
                params = {
                    "patient": ",".join(batch),
                    "_count": str(_PAGE_SIZE),
                }
                yield from self._paginate(client, resource_type, params)
        elif self.patient_ids and resource_type == "Patient":
            # Fetch specific patients by _id search.
            for batch in _batched(self.patient_ids, _PATIENT_ID_BATCH_SIZE):
                params = {
                    "_id": ",".join(batch),
                    "_count": str(_PAGE_SIZE),
                }
                yield from self._paginate(client, resource_type, params)
        else:
            yield from self._paginate(
                client, resource_type, {"_count": str(_PAGE_SIZE)}
            )

    def _paginate(
        self,
        client: httpx.Client,
        resource_type: str,
        params: dict[str, str],
    ) -> Iterator[dict]:
        """Follow Bundle pagination links, yielding individual resources."""
        url: str | None = f"{self.base_url}/{resource_type}"
        page = 0
        total_yielded = 0

        while url is not None:
            page += 1
            data = self._get_json(client, url, params if page == 1 else None)
            if data is None:
                break

            entries = data.get("entry", [])
            for entry in entries:
                resource = entry.get("resource")
                if resource:
                    yield resource
                    total_yielded += 1

            logger.debug(
                "%s page %d — %d entries (total so far: %d)",
                resource_type,
                page,
                len(entries),
                total_yielded,
            )

            # Find the "next" pagination link.
            url = _next_link(data)

        logger.info("Finished %s — yielded %d resources", resource_type, total_yielded)

    def _get_json(
        self,
        client: httpx.Client,
        url: str,
        params: dict[str, str] | None = None,
    ) -> dict | None:
        """GET a URL with auth, retries, and exponential backoff.

        Returns the parsed JSON body, or *None* if the request fails after
        all retries (non-auth errors).  Raises :class:`_AuthError` on 401/403.
        """
        headers = self._build_headers()

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning(
                    "HTTP transport error (attempt %d/%d): %s",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )
                self._backoff(attempt)
                continue

            if resp.status_code in (401, 403):
                raise _AuthError(
                    f"{resp.status_code} {resp.reason_phrase} from {url}"
                )

            if resp.status_code == 429 or resp.status_code >= 500:
                logger.warning(
                    "%d from %s (attempt %d/%d)",
                    resp.status_code,
                    url,
                    attempt,
                    _MAX_RETRIES,
                )
                self._backoff(attempt)
                # Refresh token in case of long waits.
                headers = self._build_headers()
                continue

            if not resp.is_success:
                logger.error(
                    "Unexpected %d from %s — skipping",
                    resp.status_code,
                    url,
                )
                return None

            return resp.json()

        logger.error("Exhausted retries for %s", url)
        return None

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/fhir+json"}
        if self.token_provider is not None:
            token = self.token_provider()
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _backoff(attempt: int) -> None:
        delay = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _BACKOFF_MAX)
        logger.debug("Backing off %.1fs before retry", delay)
        time.sleep(delay)


# ------------------------------------------------------------------
# Private utilities
# ------------------------------------------------------------------


class _AuthError(Exception):
    """Raised internally on 401/403 so callers can distinguish auth failures."""


def _next_link(bundle: dict) -> str | None:
    """Extract the ``next`` pagination URL from a FHIR Bundle."""
    for link in bundle.get("link", []):
        if link.get("relation") == "next":
            return link.get("url")
    return None


def _batched(items: list[str], size: int) -> Iterator[list[str]]:
    """Yield successive chunks of *items* with at most *size* elements."""
    for i in range(0, len(items), size):
        yield items[i : i + size]
