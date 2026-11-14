from app.connectors.workday import _merge_workday_locations


def test_merges_additional_locations():
    raw = _merge_workday_locations(
        {
            "location": "Noida",
            "additionalLocations": ["Bangalore"],
            "country": {"descriptor": "India"},
        },
        fallback="2 Locations",
    )
    assert raw is not None
    assert "Noida" in raw
    assert "Bangalore" in raw
    assert ";" in raw
    assert "India" in raw


def test_ignores_n_locations_fallback():
    raw = _merge_workday_locations({"location": None, "additionalLocations": []}, "2 Locations")
    assert raw is None
