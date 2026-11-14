from app.connectors.page_scraper import (
    discover_ats_boards,
    listing_follow_urls,
    stubs_from_greenhouse_payload,
    stubs_from_html,
    stubs_from_json_payload,
)


def test_stubs_from_html_job_links():
    html = """
    <html><body>
      <a href="/careers/jobs/senior-backend-engineer">Senior Backend Engineer</a>
      <a href="/about">About us</a>
      <a href="/careers">Careers</a>
      <a href="/jobs/staff-software-engineer-platform">Staff Software Engineer, Platform</a>
      <a href="/careers/application?gh_jid=123">Principal Software Engineer</a>
    </body></html>
    """
    stubs = stubs_from_html(html, "https://example.com/careers")
    titles = {s.title for s in stubs}
    assert "Senior Backend Engineer" in titles
    assert "Staff Software Engineer, Platform" in titles
    assert "Principal Software Engineer" in titles
    assert "About us" not in titles


def test_stubs_from_json_nested():
    data = {
        "data": {
            "jobs": [
                {
                    "id": "1",
                    "title": "Machine Learning Engineer",
                    "url": "/jobs/ml-1",
                    "location": "Bengaluru",
                },
                {"id": "2", "title": "Account Executive", "url": "/jobs/ae-1"},
            ]
        }
    }
    stubs = stubs_from_json_payload(data, "https://example.com")
    assert len(stubs) == 1
    assert stubs[0].title.startswith("Machine Learning")
    assert stubs[0].location_raw == "Bengaluru"


def test_stubs_from_json_rejects_award_noise():
    data = {
        "items": [
            {"title": "Software Advice Front Runner Award", "name": "award"},
            {
                "id": "99",
                "title": "Staff Software Engineer",
                "absolute_url": "https://example.com/jobs/99",
            },
        ]
    }
    stubs = stubs_from_json_payload(data, "https://example.com")
    assert len(stubs) == 1
    assert stubs[0].title == "Staff Software Engineer"


def test_discover_greenhouse_boards_from_xhr():
    boards = discover_ats_boards(
        "https://boards-api.greenhouse.io/v1/boards/ably30/departments",
        "https://job-boards.eu.greenhouse.io/ably30/jobs/123",
    )
    assert "ably30" in boards["greenhouse"]


def test_listing_follow_and_greenhouse_payload():
    html = """
    <a href="/company/careers/job-listing/">View open roles</a>
    <a href="/feed/jobs.xml">RSS</a>
    """
    follows = listing_follow_urls(html, "https://www.okta.com/company/careers/")
    assert any("job-listing" in u for u in follows)
    stubs = stubs_from_greenhouse_payload(
        {
            "jobs": [
                {
                    "id": 1,
                    "title": "Software Engineer",
                    "absolute_url": "https://boards.greenhouse.io/x/jobs/1",
                    "location": {"name": "Remote"},
                }
            ]
        },
        "x",
    )
    assert len(stubs) == 1
    assert stubs[0].location_raw == "Remote"
