"""Re-extract multi-city location_raw from stored ATS payloads."""

from __future__ import annotations

from typing import Any

from app.connectors.amazon import _location as amazon_location
from app.connectors.ashby import _location as ashby_location
from app.connectors.generic import _loc_from_raw as generic_location
from app.connectors.greenhouse import _location as greenhouse_location
from app.connectors.lever import _location as lever_location
from app.connectors.oracle import _location as oracle_location
from app.connectors.smartrecruiters import _location as smartrecruiters_location
from app.connectors.workday import _merge_workday_locations


def location_raw_from_raw(
    ats_type: str,
    raw: dict[str, Any] | None,
    fallback: str | None = None,
) -> str | None:
    """ATS-agnostic multi-location extractor used by ingest + backfill."""
    if not isinstance(raw, dict) or not raw:
        return None

    kind = (ats_type or "").lower()
    if kind == "greenhouse":
        return greenhouse_location(raw)
    if kind == "lever":
        return lever_location(raw)
    if kind == "ashby":
        return ashby_location(raw)
    if kind == "amazon":
        return amazon_location(raw)
    if kind == "smartrecruiters":
        return smartrecruiters_location(raw)
    if kind == "oracle":
        return oracle_location(raw)
    if kind == "generic":
        return generic_location(raw)
    if kind == "workday":
        info = raw.get("jobPostingInfo") if isinstance(raw.get("jobPostingInfo"), dict) else raw
        return _merge_workday_locations(info, fallback)
    return None
