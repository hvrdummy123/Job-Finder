from app.connectors.amazon import _location as amazon_location
from app.connectors.ashby import _location as ashby_location
from app.connectors.generic import _loc_from_raw as generic_location
from app.connectors.greenhouse import _location as greenhouse_location
from app.connectors.lever import _location as lever_location
from app.connectors.util import join_locations
from app.connectors.workday import _merge_workday_locations
from app.ingest.ats_locations import location_raw_from_raw
from app.ingest.locations import locations_payload, split_location_raw


def test_join_locations_dedupes():
    assert join_locations("Noida", "Bangalore", "noida") == "Noida; Bangalore"
    assert join_locations("2 Locations") is None


def test_greenhouse_uses_offices():
    raw = {
        "location": {"name": "Chicago, Seattle, NYC"},
        "offices": [
            {"name": "US-Chicago", "location": "Chicago, Illinois, United States"},
            {"name": "US-Seattle", "location": "Seattle, Washington, United States"},
            {"name": "US-NYC", "location": "New York, New York, United States"},
        ],
    }
    loc = greenhouse_location(raw)
    assert loc is not None
    assert "Chicago" in loc and "Seattle" in loc and "New York" in loc
    assert ";" in loc
    assert len(locations_payload(loc)) >= 3


def test_lever_all_locations():
    raw = {
        "categories": {
            "location": "New York, NY",
            "allLocations": ["New York, NY", "San Francisco, CA"],
        }
    }
    loc = lever_location(raw)
    assert loc is not None
    assert "San Francisco" in loc
    assert len(split_location_raw(loc)) == 2


def test_ashby_secondary_locations():
    raw = {
        "location": "San Francisco",
        "secondaryLocations": [{"location": "London, UK"}],
    }
    loc = ashby_location(raw)
    assert loc == "San Francisco; London, UK"


def test_amazon_locations_json_array():
    raw = {
        "location": "US, TX, Austin",
        "city": "Austin",
        "normalized_location": "Austin, Texas, USA",
        "locations": [
            '{"city":"Austin","location":"US, TX, Austin","normalizedLocation":"Austin, Texas, USA"}',
            '{"city":"Cupertino","location":"US, CA, Cupertino","normalizedLocation":"Cupertino, California, USA"}',
        ],
    }
    loc = amazon_location(raw)
    assert loc == "US, TX, Austin; US, CA, Cupertino"
    assert len(locations_payload(loc)) == 2


def test_amazon_single_location_no_dupes():
    raw = {
        "location": "IN, KA, Bengaluru",
        "city": "Bengaluru",
        "normalized_location": "Bengaluru, Karnataka, IND",
        "locations": [
            '{"city":"Bengaluru","location":"IN, KA, Bengaluru","normalizedLocation":"Bengaluru, Karnataka, IND"}',
        ],
    }
    loc = amazon_location(raw)
    assert loc == "IN, KA, Bengaluru"
    assert len(locations_payload(loc)) == 1


def test_generic_job_location_list():
    raw = {
        "jobLocation": [
            {"address": {"addressLocality": "Bengaluru", "addressCountry": "IN"}},
            {"address": {"addressLocality": "Hyderabad", "addressCountry": "IN"}},
        ]
    }
    loc = generic_location(raw)
    assert loc is not None
    assert "Bengaluru" in loc and "Hyderabad" in loc


def test_workday_additional_locations():
    loc = _merge_workday_locations(
        {
            "location": "Noida",
            "additionalLocations": ["Bangalore"],
            "country": {"descriptor": "India"},
        },
        "2 Locations",
    )
    assert loc is not None
    payload = locations_payload(loc)
    cities = {p.get("city") or p.get("city_normalized") for p in payload}
    assert "Noida" in cities
    assert "Bangalore" in cities or "Bengaluru" in cities


def test_dispatcher_routes_ats_types():
    assert location_raw_from_raw(
        "lever",
        {"categories": {"allLocations": ["A", "B"], "location": "A"}},
    ) == "A; B"
