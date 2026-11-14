"""Detection and multi-location must work for arbitrary companies, not hard-coded names."""

from app.api.companies import KNOWN_ATS
from app.connectors.util import join_locations
from app.detect import _slug_candidates
from app.ingest.ats_locations import location_raw_from_raw


def test_known_ats_includes_new_connectors():
    assert "smartrecruiters" in KNOWN_ATS
    assert "oracle" in KNOWN_ATS
    assert "greenhouse" in KNOWN_ATS


def test_slug_candidates_from_company_name_not_hardcoded():
    cands = _slug_candidates("https://www.stripe.com/careers", "Stripe Labs Inc")
    assert any(c.lower() in {"stripe", "stripelabs"} for c in cands)
    assert "Canva" not in cands  # no unrelated company leakage
    # demo hosts must not become Greenhouse board slugs
    assert "example" not in [c.lower() for c in _slug_candidates("https://www.example.com/careers", "Fake Co")]


def test_multi_location_extractors_cover_all_ats():
    gh = location_raw_from_raw(
        "greenhouse",
        {
            "location": {"name": "Remote"},
            "offices": [
                {"location": "Chicago, IL"},
                {"location": "Seattle, WA"},
            ],
        },
    )
    assert gh and "Chicago" in gh and "Seattle" in gh

    lever = location_raw_from_raw(
        "lever",
        {"categories": {"location": "NYC", "allLocations": ["NYC", "SF"]}},
    )
    assert lever and "SF" in lever

    ashby = location_raw_from_raw(
        "ashby",
        {"location": "SF", "secondaryLocations": [{"location": "London"}]},
    )
    assert ashby == "SF; London"

    amazon = location_raw_from_raw(
        "amazon",
        {
            "locations": [
                '{"location":"US, WA, Seattle"}',
                '{"location":"US, NY, New York"}',
            ]
        },
    )
    assert amazon and "Seattle" in amazon and "New York" in amazon

    wd = location_raw_from_raw(
        "workday",
        {
            "jobPostingInfo": {
                "location": "Noida",
                "additionalLocations": ["Bangalore"],
                "country": {"descriptor": "India"},
            }
        },
    )
    assert wd and "Noida" in wd and "Bangalore" in wd

    oracle = location_raw_from_raw(
        "oracle",
        {
            "PrimaryLocation": "Kuala Lumpur",
            "secondaryLocations": [{"Location": "Singapore"}],
        },
    )
    assert oracle and "Singapore" in oracle

    sr = location_raw_from_raw(
        "smartrecruiters",
        {"location": {"fullLocation": "London, United Kingdom"}},
    )
    assert sr and "London" in sr


def test_join_locations_is_generic():
    assert join_locations("A", "B", "a") == "A; B"
