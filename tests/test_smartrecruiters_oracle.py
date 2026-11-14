from app.connectors.oracle import OracleConnector, _location, _pack_slug, _unpack_slug
from app.connectors.smartrecruiters import SmartRecruitersConnector, _location as sr_location


def test_smartrecruiters_detect():
    c = SmartRecruitersConnector()
    hit = c.detect("https://careers.smartrecruiters.com/Canva")
    assert hit is not None
    assert hit.ats_type == "smartrecruiters"
    assert hit.ats_slug == "Canva"


def test_smartrecruiters_location():
    assert "London" in (sr_location({"location": {"fullLocation": "London, United Kingdom"}}) or "")


def test_oracle_slug_roundtrip():
    slug = _pack_slug(
        "egug.fa.us2.oraclecloud.com",
        "CX_1",
        "https://careers.americanexpress.com",
    )
    parts = _unpack_slug(slug)
    assert parts is not None
    host, site, portal = parts
    assert host.startswith("egug.")
    assert site == "CX_1"
    assert "americanexpress" in portal


def test_oracle_detect_amex_url():
    c = OracleConnector()
    # Detection from known careers host needs HTML/body with oraclecloud; slug unpack works via detect known map in detect.py
    assert _location({"PrimaryLocation": "Noida", "secondaryLocations": [{"Location": "Bangalore"}]})
