from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app import db

_COUNTRY_CODES = {
    "in": "India",
    "us": "United States",
    "usa": "United States",
    "gb": "United Kingdom",
    "uk": "United Kingdom",
    "ca": "Canada",
    "de": "Germany",
    "ie": "Ireland",
    "il": "Israel",
    "pl": "Poland",
    "sg": "Singapore",
    "au": "Australia",
    "jp": "Japan",
    "nl": "Netherlands",
    "fr": "France",
    "es": "Spain",
    "br": "Brazil",
    "mx": "Mexico",
    "ae": "United Arab Emirates",
}

_STATE_CODES = {
    "ka": "Karnataka",
    "ts": "Telangana",
    "mh": "Maharashtra",
    "tn": "Tamil Nadu",
    "dl": "Delhi",
    "hr": "Haryana",
    "up": "Uttar Pradesh",
    "wb": "West Bengal",
    "wa": "Washington",
    "tx": "Texas",
    "ny": "New York",
    "va": "Virginia",
    "co": "Colorado",
    "ma": "Massachusetts",
    "bc": "British Columbia",
    "on": "Ontario",
    # CA as state is California — only apply when country is US
    "ca": "California",
}


@dataclass(slots=True)
class NormalizedLocation:
    city: str | None = None
    state: str | None = None
    country: str | None = None
    city_normalized: str | None = None
    workplace_hint: str | None = None
    raw: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}


@dataclass(slots=True)
class LocationQueryExpansion:
    """Patterns used to match a user location filter term against job rows."""

    term: str
    cities: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    raw_substrings: list[str] = field(default_factory=list)


_CACHE: dict[str, tuple[str | None, str | None, str | None, str | None]] | None = None


async def load_alias_cache() -> None:
    global _CACHE
    rows = await db.fetch(
        "SELECT alias, canonical_city, canonical_state, canonical_country, workplace_hint FROM location_aliases"
    )
    _CACHE = {
        row["alias"]: (
            row["canonical_city"],
            row["canonical_state"],
            row["canonical_country"],
            row["workplace_hint"],
        )
        for row in rows
    }


def split_location_raw(location_raw: str | None) -> list[str]:
    """Split a job location string that may list multiple places."""
    if not location_raw:
        return []
    raw = location_raw.strip()
    if not raw:
        return []
    # Don't split ISO triples like "IN, KA, Bengaluru" on commas
    if re.match(r"^[A-Za-z]{2},\s*[A-Za-z0-9]{1,3},\s*.+$", raw) and raw.count(",") == 2:
        return [raw]
    if re.match(r"^[A-Za-z]{2},\s*[^,]+$", raw) and raw.count(",") == 1:
        return [raw]

    parts = re.split(r"\s*[;|•]\s*|\s+/\s+|\s+&\s+|\s+ and \s+", raw, flags=re.I)
    out: list[str] = []
    for part in parts:
        part = part.strip(" ·-")
        if part:
            out.append(part)
    return out or [raw]


def normalize_location(location_raw: str | None) -> NormalizedLocation:
    if not location_raw:
        return NormalizedLocation()

    raw = location_raw.strip()
    lower = re.sub(r"\s+", " ", raw.lower())

    # Amazon / Workday style: "IN, KA, Bengaluru" or "US, WA, Seattle"
    m3 = re.match(r"^([A-Za-z]{2})\s*,\s*([A-Za-z0-9]{1,3})\s*,\s*(.+)$", raw)
    if m3:
        cc, st, city = m3.group(1), m3.group(2), m3.group(3).strip()
        country = _COUNTRY_CODES.get(cc.lower(), cc.upper())
        state = _expand_state(st, country)
        city_norm = _alias_city(city) or city
        return NormalizedLocation(
            city=city_norm,
            state=state,
            country=country,
            city_normalized=city_norm,
            workplace_hint=_workplace_hint(lower),
            raw=raw,
        )

    # "GB, London"
    m2 = re.match(r"^([A-Za-z]{2})\s*,\s*([^,]+)$", raw)
    if m2 and m2.group(1).lower() in _COUNTRY_CODES:
        cc, city = m2.group(1), m2.group(2).strip()
        country = _COUNTRY_CODES[cc.lower()]
        city_norm = _alias_city(city) or city
        return NormalizedLocation(
            city=city_norm,
            country=country,
            city_normalized=city_norm,
            workplace_hint=_workplace_hint(lower),
            raw=raw,
        )

    if _CACHE:
        if lower in _CACHE:
            city, state, country, hint = _CACHE[lower]
            return NormalizedLocation(
                city=city,
                state=state,
                country=country,
                city_normalized=city,
                workplace_hint=hint or _workplace_hint(lower),
                raw=raw,
            )
        first = lower.split(",")[0].strip()
        if first in _CACHE:
            city, state, country, hint = _CACHE[first]
            parts = [p.strip() for p in raw.split(",")]
            rest_country = None
            if len(parts) >= 2 and not country:
                rest_country = _COUNTRY_CODES.get(parts[-1].lower(), parts[-1])
            return NormalizedLocation(
                city=city,
                state=state,
                country=country or rest_country,
                city_normalized=city,
                workplace_hint=hint or _workplace_hint(lower),
                raw=raw,
            )

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    city = parts[0] if parts else None
    state = None
    country = None
    if len(parts) == 2:
        maybe = parts[1]
        if len(maybe) <= 3 and maybe.isalpha():
            country = _COUNTRY_CODES.get(maybe.lower(), maybe)
        else:
            country = _COUNTRY_CODES.get(maybe.lower(), maybe)
    elif len(parts) >= 3:
        state = parts[1]
        country = _COUNTRY_CODES.get(parts[-1].lower(), parts[-1])

    city_norm = _alias_city(city) if city else None
    return NormalizedLocation(
        city=city_norm or city,
        state=state,
        country=country,
        city_normalized=city_norm or city,
        workplace_hint=_workplace_hint(lower),
        raw=raw,
    )


def normalize_all_locations(location_raw: str | None) -> list[NormalizedLocation]:
    parts = split_location_raw(location_raw)
    if not parts:
        return []
    return [normalize_location(p) for p in parts]


def locations_payload(location_raw: str | None) -> list[dict[str, Any]]:
    return [loc.as_dict() for loc in normalize_all_locations(location_raw)]


def primary_location(location_raw: str | None) -> NormalizedLocation:
    locs = normalize_all_locations(location_raw)
    return locs[0] if locs else NormalizedLocation()


async def expand_location_terms(terms: list[str]) -> list[LocationQueryExpansion]:
    """Expand user filter terms using aliases so 'India' matches Bangalore jobs.

    - Country term → that country + all known cities/states in it
    - State/region term → that state + cities in it (not the whole country)
    - City term → city aliases only (not the whole country)
    """
    if _CACHE is None:
        await load_alias_cache()
    cache = _CACHE or {}
    expansions: list[LocationQueryExpansion] = []

    for raw_term in terms:
        term = re.sub(r"\s+", " ", (raw_term or "").strip().lower())
        if not term:
            continue
        exp = LocationQueryExpansion(term=term, raw_substrings=[term])

        hit = cache.get(term)
        country_name = _COUNTRY_CODES.get(term)
        if hit:
            city, state, country, _hint = hit
            if city:
                kind = "city"
            elif state:
                kind = "state"
            else:
                kind = "country"
                country_name = country or country_name
        elif country_name:
            kind = "country"
        else:
            # Free text — treat as city/raw substring; also try title-case country match
            kind = "raw"
            for _alias, (_c, _s, country, _h) in cache.items():
                if country and country.lower() == term:
                    kind = "country"
                    country_name = country
                    break
            if kind == "raw":
                for _alias, (_c, state, _co, _h) in cache.items():
                    if state and state.lower() == term:
                        kind = "state"
                        hit = (None, state, _co, None)
                        break

        if kind == "country":
            country_name = country_name or (hit[2] if hit else None)
            if country_name:
                exp.countries.append(country_name)
                exp.raw_substrings.append(country_name)
            # ISO code for Amazon-style "IN, KA, Bengaluru"
            for code, name in _COUNTRY_CODES.items():
                if country_name and name.lower() == country_name.lower():
                    exp.raw_substrings.append(code.upper())
            for alias, (city, state, country, _hint) in cache.items():
                if country and country_name and country.lower() == country_name.lower():
                    if city:
                        exp.cities.append(city)
                    if state:
                        exp.states.append(state)
                    if len(alias) > 3:
                        exp.raw_substrings.append(alias)
        elif kind == "state":
            state_name = hit[1] if hit else term.title()
            exp.states.append(state_name)
            if len(state_name) > 3:
                exp.raw_substrings.append(state_name)
            for alias, (city, state, country, _hint) in cache.items():
                if state and state.lower() == state_name.lower():
                    if city:
                        exp.cities.append(city)
                    if len(alias) > 3:
                        exp.raw_substrings.append(alias)
                    elif len(alias) <= 3:
                        exp.raw_substrings.append(alias.upper())
        elif kind == "city":
            city_name = hit[0] if hit else term
            exp.cities.append(city_name)
            for alias, (city, state, country, _hint) in cache.items():
                if city and city.lower() == city_name.lower():
                    exp.cities.append(city)
                    if len(alias) > 2:
                        exp.raw_substrings.append(alias)
        else:
            exp.cities.append(term.title())
            exp.raw_substrings.append(term)

        exp.cities = _uniq(exp.cities)
        exp.states = _uniq(exp.states)
        exp.countries = _uniq(exp.countries)
        exp.raw_substrings = _uniq(exp.raw_substrings)
        expansions.append(exp)

    return expansions


def _alias_city(value: str | None) -> str | None:
    if not value or not _CACHE:
        return value
    hit = _CACHE.get(value.strip().lower())
    if hit and hit[0]:
        return hit[0]
    return value


def _expand_state(code: str, country: str | None) -> str:
    low = code.strip().lower()
    if low == "ca" and country and country.lower() in {"canada"}:
        return code.upper()
    return _STATE_CODES.get(low, code.strip())


def _workplace_hint(lower: str) -> str | None:
    if re.search(r"\bremote\b", lower):
        return "remote"
    if re.search(r"\bhybrid\b", lower):
        return "hybrid"
    return None


def _uniq(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if not v:
            continue
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out
