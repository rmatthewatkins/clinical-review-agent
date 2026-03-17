"""FHIR resource parser and PostgreSQL loader.

Accepts any FhirSource (file-based, API, etc.) and normalizes FHIR resources
into the shared PostgreSQL schema.
"""

import logging

from src.data.sources.base import FhirSource, ref, code_display, extension_value
from src.schema import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resource extractors — each returns a tuple matching its table columns
# ---------------------------------------------------------------------------

def _parse_patient(r: dict) -> tuple:
    addr = (r.get("address") or [{}])[0]
    exts = r.get("extension", [])
    lat = lng = None
    geo = addr.get("extension", [])
    for g in geo:
        if "geolocation" in g.get("url", ""):
            for sub in g.get("extension", []):
                if sub.get("url") == "latitude":
                    lat = sub.get("valueDecimal")
                elif sub.get("url") == "longitude":
                    lng = sub.get("valueDecimal")
    return (
        r["id"],
        r.get("birthDate"),
        r.get("gender"),
        extension_value(exts, "us-core-race"),
        extension_value(exts, "us-core-ethnicity"),
        addr.get("city"),
        addr.get("state"),
        lat,
        lng,
    )


def _parse_encounter(r: dict) -> tuple:
    period = r.get("period", {})

    # Reason: prefer reasonCode, fall back to reasonReference
    reason_code = reason_display = None
    reasons = r.get("reasonCode", [])
    if reasons:
        reason_code, reason_display = code_display(reasons[0])
    elif r.get("reasonReference"):
        # Epic may use reasonReference instead of reasonCode
        reason_refs = r["reasonReference"]
        if isinstance(reason_refs, list) and reason_refs:
            rr = reason_refs[0]
        elif isinstance(reason_refs, dict):
            rr = reason_refs
        else:
            rr = {}
        reason_display = rr.get("display")
        # Try to extract a code from the reference's identifier
        reason_code = ref(rr.get("reference"))

    discharge = None
    hosp = r.get("hospitalization", {})
    if hosp:
        dd = hosp.get("dischargeDisposition", {})
        codings = dd.get("coding", [])
        discharge = codings[0].get("display") if codings else dd.get("text")

    # Determine encounter type: prefer class code for inpatient detection
    enc_type = None
    enc_class = r.get("class", {})
    if isinstance(enc_class, dict):
        enc_code = enc_class.get("code", "")
    else:
        enc_code = ""

    type_list = r.get("type", [])
    if type_list:
        _, enc_type = code_display(type_list[0])

    # Normalise to 'inpatient' / 'ambulatory' / etc. for easy querying
    if enc_code == "IMP" or (enc_type and "inpatient" in enc_type.lower()):
        enc_type_norm = "inpatient"
    elif enc_code == "AMB":
        enc_type_norm = "ambulatory"
    elif enc_code == "EMER":
        enc_type_norm = "emergency"
    else:
        enc_type_norm = enc_type or enc_code

    return (
        r["id"],
        ref(r.get("subject", {}).get("reference")),
        enc_type_norm,
        period.get("start"),
        period.get("end"),
        reason_code,
        reason_display,
        discharge,
    )


def _parse_condition(r: dict) -> tuple:
    code, display = code_display(r.get("code"))
    cs = r.get("clinicalStatus", {})
    clinical_status = None
    cs_codings = cs.get("coding", []) if cs else []
    if cs_codings:
        clinical_status = cs_codings[0].get("code")
    return (
        r["id"],
        ref(r.get("subject", {}).get("reference")),
        ref(r.get("encounter", {}).get("reference")),
        code,
        display,
        r.get("onsetDateTime"),
        r.get("abatementDateTime"),
        clinical_status,
    )


def _parse_medication(r: dict) -> tuple:
    # Prefer medicationCodeableConcept (Synthea), fall back to
    # medicationReference.display (Epic)
    code, display = code_display(r.get("medicationCodeableConcept"))
    if code is None and display is None:
        med_ref = r.get("medicationReference", {})
        if med_ref:
            display = med_ref.get("display")
            code = ref(med_ref.get("reference"))

    encounter_ref = r.get("encounter", {}).get("reference")
    # Some FHIR versions nest encounter under context
    if not encounter_ref:
        encounter_ref = r.get("context", {}).get("reference")
    return (
        r["id"],
        ref(r.get("subject", {}).get("reference")),
        ref(encounter_ref),
        code,
        display,
        r.get("authoredOn"),
        None,  # end not typically in MedicationRequest
        r.get("status"),
    )


def _parse_observation(r: dict) -> tuple:
    code, display = code_display(r.get("code"))
    vq = r.get("valueQuantity", {})
    value = unit = None
    if vq:
        value = str(vq["value"]) if "value" in vq else None
        unit = vq.get("unit")
    else:
        vc = r.get("valueCodeableConcept")
        if vc:
            _, value = code_display(vc)
        elif r.get("component"):
            # Multi-part observations (e.g., blood pressure with
            # systolic/diastolic components). Concatenate component values.
            parts = []
            for comp in r["component"]:
                comp_code, comp_display = code_display(comp.get("code"))
                comp_vq = comp.get("valueQuantity", {})
                if comp_vq and "value" in comp_vq:
                    parts.append(f"{comp_display or comp_code}: {comp_vq['value']} {comp_vq.get('unit', '')}")
            if parts:
                value = "; ".join(parts)
    return (
        r["id"],
        ref(r.get("subject", {}).get("reference")),
        ref(r.get("encounter", {}).get("reference")),
        code,
        display,
        value,
        unit,
        r.get("effectiveDateTime"),
    )


def _parse_procedure(r: dict) -> tuple:
    code, display = code_display(r.get("code"))
    date = r.get("performedDateTime")
    if not date:
        pp = r.get("performedPeriod", {})
        date = pp.get("start") if pp else None
    return (
        r["id"],
        ref(r.get("subject", {}).get("reference")),
        ref(r.get("encounter", {}).get("reference")),
        code,
        display,
        date,
    )


def _parse_careplan(r: dict) -> tuple:
    cats = r.get("category", [])
    code = display = None
    if cats:
        code, display = code_display(cats[0])
    period = r.get("period", {})
    # encounter reference may be under context or encounter
    enc_ref = None
    enc_field = r.get("encounter", r.get("context"))
    if isinstance(enc_field, dict):
        enc_ref = enc_field.get("reference")
    elif isinstance(enc_field, list) and enc_field:
        enc_ref = enc_field[0].get("reference")
    return (
        r["id"],
        ref(r.get("subject", {}).get("reference")),
        ref(enc_ref),
        code,
        display,
        period.get("start"),
        period.get("end"),
        r.get("status"),
    )


# ---------------------------------------------------------------------------
# Parser registry
# ---------------------------------------------------------------------------

_PARSERS: dict[str, tuple[str, callable]] = {
    "Patient":           ("patients",    _parse_patient),
    "Encounter":         ("encounters",  _parse_encounter),
    "Condition":         ("conditions",  _parse_condition),
    "MedicationRequest": ("medications", _parse_medication),
    "Observation":       ("observations", _parse_observation),
    "Procedure":         ("procedures",  _parse_procedure),
    "CarePlan":          ("care_plans",  _parse_careplan),
}

_PLACEHOLDERS = {
    "patients":     9,
    "encounters":   8,
    "conditions":   8,
    "medications":  8,
    "observations": 8,
    "procedures":   6,
    "care_plans":   8,
}


# ---------------------------------------------------------------------------
# Main ingest
# ---------------------------------------------------------------------------

def run_ingest(source: FhirSource | str) -> None:
    """Parse FHIR resources from *source* into PostgreSQL.

    Parameters
    ----------
    source : FhirSource or str
        A FhirSource instance, or a directory path (str) for backwards
        compatibility with the file-based Synthea ingest.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Backwards compat: accept a directory path string
    if isinstance(source, str):
        from src.data.sources.synthea_file import SyntheaFileSource
        source = SyntheaFileSource(source)

    conn = get_connection()
    # Defer FK checks during bulk load to avoid ordering issues
    # with cross-bundle references. Checked at commit time.
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    counts: dict[str, int] = {t: 0 for t in _PLACEHOLDERS}

    # Accumulate rows per table, then batch-insert
    rows: dict[str, list[tuple]] = {t: [] for t in _PLACEHOLDERS}

    for resource in source.iter_resources():
        rtype = resource.get("resourceType")
        if rtype not in _PARSERS:
            continue
        table, parser = _PARSERS[rtype]
        try:
            rows[table].append(parser(resource))
        except Exception:
            logger.exception("Failed to parse %s %s", rtype, resource.get("id"))

    # Batch insert in FK-safe order: parents before children
    insert_order = [
        "patients", "encounters", "conditions", "medications",
        "observations", "procedures", "care_plans",
    ]
    for table in insert_order:
        data = rows[table]
        if not data:
            continue
        ph = ",".join(["%s"] * _PLACEHOLDERS[table])
        sql = f"INSERT INTO {table} VALUES ({ph}) ON CONFLICT DO NOTHING"
        with conn.cursor() as cur:
            cur.executemany(sql, data)
        counts[table] = len(data)

    conn.commit()
    conn.close()

    logger.info("Ingest complete. Row counts attempted (ON CONFLICT DO NOTHING):")
    for table, c in counts.items():
        logger.info("  %-15s %d", table, c)
