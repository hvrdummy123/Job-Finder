from app.connectors.portals.indeed import _parse_rss

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Indeed RSS</title>
    <item>
      <title>Backend Engineer - Acme Corp</title>
      <link>https://www.indeed.com/viewjob?jk=abc123</link>
      <guid>https://www.indeed.com/viewjob?jk=abc123</guid>
      <description><![CDATA[<p>Remote role with 3 years experience</p>]]></description>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def test_parse_indeed_rss():
    stubs = _parse_rss(SAMPLE, "www.indeed.com")
    assert len(stubs) == 1
    assert stubs[0].title == "Backend Engineer"
    assert stubs[0].raw.get("employer") == "Acme Corp"
    assert stubs[0].url and "viewjob" in stubs[0].url
    assert stubs[0].raw.get("portal") == "indeed"
