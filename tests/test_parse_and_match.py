from app.ingest.match import job_matches_search
from app.ingest.parse_jd import parse_jd
from app.ingest.locations import normalize_location


def test_parse_yoe_range():
    parsed = parse_jd("Engineer", "Must have 2-4 years of experience with Python.")
    assert parsed.yoe_min == 2
    assert parsed.yoe_max == 4
    assert "python" in (parsed.skills or [])


def test_parse_yoe_plus():
    parsed = parse_jd("Senior Engineer", "5+ years of experience required.")
    assert parsed.yoe_min == 5
    assert parsed.yoe_max is None
    assert parsed.seniority == "senior"


def test_parse_unspecified_yoe():
    parsed = parse_jd("Designer", "Join our product design team in a hybrid setting.")
    assert parsed.yoe_min is None
    assert parsed.yoe_max is None
    assert parsed.workplace_type == "hybrid"


def test_match_includes_unspecified_yoe_by_default():
    job = {
        "title": "Engineer",
        "description_text": "Build things",
        "yoe_min": None,
        "yoe_max": None,
        "workplace_type": "remote",
    }
    search = {
        "include_terms": ["engineer"],
        "exclude_terms": [],
        "locations": [],
        "yoe_min": 0,
        "yoe_max": 2,
        "include_unspecified_yoe": True,
        "workplace_types": [],
    }
    assert job_matches_search(job, search) is True


def test_match_can_exclude_unspecified_yoe():
    job = {
        "title": "Engineer",
        "description_text": "Build things",
        "yoe_min": None,
        "yoe_max": None,
        "workplace_type": "remote",
    }
    search = {
        "include_terms": ["engineer"],
        "exclude_terms": [],
        "locations": [],
        "yoe_min": 0,
        "yoe_max": 2,
        "include_unspecified_yoe": False,
        "workplace_types": [],
    }
    assert job_matches_search(job, search) is False


def test_match_exclude_terms():
    job = {
        "title": "Staff Engineer",
        "description_text": "Lead the platform",
        "yoe_min": 8,
        "yoe_max": 12,
        "workplace_type": "onsite",
    }
    search = {
        "include_terms": ["engineer"],
        "exclude_terms": ["staff"],
        "locations": [],
        "include_unspecified_yoe": True,
        "workplace_types": [],
    }
    assert job_matches_search(job, search) is False


def test_location_alias_bangalore(monkeypatch):
    from app.ingest import locations as locmod

    monkeypatch.setattr(
        locmod,
        "_CACHE",
        {
            "bangalore": ("Bengaluru", "Karnataka", "India", None),
            "remote - india": (None, None, "India", "remote"),
        },
    )
    n = normalize_location("Bangalore")
    assert n.city_normalized == "Bengaluru"
    assert n.country == "India"

    r = normalize_location("Remote - India")
    assert r.workplace_hint == "remote"
    assert r.country == "India"

    amazon = normalize_location("IN, KA, Bengaluru")
    assert amazon.country == "India"
    assert amazon.state == "Karnataka"
    assert amazon.city_normalized == "Bengaluru"

    us = normalize_location("US, WA, Seattle")
    assert us.country == "United States"
    assert us.state == "Washington"
    assert us.city == "Seattle"
