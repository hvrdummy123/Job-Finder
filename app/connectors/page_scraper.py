"""Scrape careers listing pages without a public ATS API.

Strategies (cheapest first):
1. Parse static HTML for job links / cards
2. Playwright: capture JSON XHR while rendering, then DOM scrape,
   optionally click "Load more" / follow "View open roles", and
   hydrate known ATS board APIs discovered in network traffic.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.connectors.util import join_locations, parse_datetime
from app.ingest.role_filter import is_tech_role, looks_like_job_url
from app.models import JobStub

log = logging.getLogger(__name__)

_JOB_HREF = re.compile(
    r"""
    /jobs?/[^/?#]+ |
    /job/[^/?#]+ |
    /positions?/[^/?#]+ |
    /openings?/[^/?#]+ |
    /careers?/application |
    /work-with-us/job/ |
    /job[-_]?detail |
    /requisition |
    job-boards(?:\.eu)?\.greenhouse\.io/ |
    boards(?:-api)?\.greenhouse\.io/ |
    jobs\.lever\.co/ |
    jobs\.ashbyhq\.com/ |
    myworkdayjobs\.com/ |
    smartrecruiters\.com/ |
    [?&](gh_jid|job[_-]?id|req[_-]?id|requisition)=
    """,
    re.I | re.X,
)

_BAD_TITLE = re.compile(
    r"""
    ^(careers?|jobs?|openings?|view\s+(all|job)|see\s+all|learn\s+more|
      apply(\s+now)?|read\s+more|home|about|teams?|life\s+at|benefits|
      culture|internship\s*program|open\s+position|subscribe|subscribed)$
    |
    \b(award|leader\s+in|front\s+runner|top\s+10|momentum\s+leader|
       gartner|g2\s+crowd|software\s+advice)\b
    """,
    re.I | re.X,
)

_LOAD_MORE = re.compile(
    r"(load\s*more|show\s*more|view\s*all|see\s*all\s*jobs|all\s*openings|"
    r"view\s*openings|see\s*our\s*open\s*roles|view\s*open\s*roles|"
    r"open\s*positions|see\s*open\s*positions)",
    re.I,
)

_LISTING_CTA = re.compile(
    r"(view|see|browse|explore).{0,24}(open\s+roles?|open\s+jobs?|open\s+positions?|"
    r"all\s+jobs?|all\s+roles?|job\s+listing|current\s+openings)|"
    r"^(open\s+roles?|open\s+jobs?|open\s+positions?|job\s+listing|all\s+openings?)$",
    re.I,
)

_GH_BOARD = re.compile(
    r"""
    boards-api(?:\.eu)?\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]{2,60})
    | job-boards(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]{2,60})
    | boards\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]{2,60})
    | greenhouse\.io/[^?\s"']*[?&]for=([A-Za-z0-9_-]{2,60})
    """,
    re.I | re.X,
)

_LEVER_BOARD = re.compile(r"(?:jobs\.)?lever\.co/([A-Za-z0-9_-]{2,60})", re.I)
_ASHBY_BOARD = re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]{2,60})", re.I)

_JOB_ID_KEYS = (
    "id",
    "uuid",
    "slug",
    "requisitionId",
    "req_id",
    "gh_jid",
    "jobId",
    "job_id",
)


def stubs_from_html(html: str, base_url: str) -> list[JobStub]:
    """Extract job stubs from rendered or static HTML."""
    if not html:
        return []
    tree = HTMLParser(html)
    found: dict[str, JobStub] = {}

    for node in tree.css(
        "[data-job], [data-job-id], [data-id].job, .job-card, .job-listing, "
        ".opening, .position, li.job, article.job, .careers-job"
    ):
        stub = _stub_from_card(node, base_url)
        if stub:
            found[stub.external_job_id] = stub

    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        abs_url = urljoin(base_url, href)
        if not _is_job_candidate_url(abs_url):
            continue
        title = _clean_title(a.text(separator=" ") or "")
        if not title:
            title = _title_from_url(abs_url)
        if not _usable_title(title):
            continue
        job_id = abs_url.split("#", 1)[0].rstrip("/")
        found[job_id] = JobStub(
            external_job_id=job_id,
            title=title,
            url=abs_url,
            absolute_url=abs_url,
        )

    return list(found.values())


def stubs_from_json_payload(data: Any, base_url: str) -> list[JobStub]:
    """Best-effort extract from arbitrary JSON job payloads."""
    jobs = _find_job_arrays(data)
    stubs: list[JobStub] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title = job.get("title") or job.get("name") or job.get("text") or job.get("jobTitle")
        if not title:
            continue
        title_s = _clean_title(str(title))
        if not _usable_title(title_s):
            continue
        url = (
            job.get("url")
            or job.get("absolute_url")
            or job.get("absoluteUrl")
            or job.get("hostedUrl")
            or job.get("jobUrl")
            or job.get("applyUrl")
            or job.get("alternate_url")
        )
        if isinstance(url, dict):
            url = url.get("url") or url.get("href")
        if isinstance(url, str) and url.startswith("/"):
            url = urljoin(base_url, url)
        url_s = str(url) if url else None
        has_job_id = any(job.get(k) not in (None, "") for k in _JOB_ID_KEYS)
        if url_s and not _is_job_candidate_url(url_s) and not has_job_id:
            continue
        if not url_s and not has_job_id:
            continue
        # Reject marketing JSON blobs that only have a vague title
        if not url_s and not any(
            k in job for k in ("absolute_url", "hostedUrl", "location", "locations", "departments")
        ):
            continue
        job_id = str(
            job.get("id")
            or job.get("uuid")
            or job.get("slug")
            or job.get("requisitionId")
            or job.get("req_id")
            or url_s
            or title_s
        )
        stubs.append(
            JobStub(
                external_job_id=job_id,
                title=title_s,
                url=url_s,
                absolute_url=url_s,
                location_raw=_location_from_job(job),
                updated_at=parse_datetime(
                    job.get("updated_at")
                    or job.get("updatedAt")
                    or job.get("createdAt")
                    or job.get("posted_date")
                    or job.get("publishedAt")
                ),
                raw=job,
            )
        )
    return stubs


def stubs_from_greenhouse_payload(data: Any, board_slug: str) -> list[JobStub]:
    """Parse Greenhouse boards-api /jobs JSON."""
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return []
    stubs: list[JobStub] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title = _clean_title(str(job.get("title") or ""))
        if not _usable_title(title):
            continue
        url = job.get("absolute_url") or (
            f"https://boards.greenhouse.io/{board_slug}/jobs/{job.get('id')}"
            if job.get("id")
            else None
        )
        loc = job.get("location")
        loc_s = loc.get("name") if isinstance(loc, dict) else (loc if isinstance(loc, str) else None)
        stubs.append(
            JobStub(
                external_job_id=str(job.get("id") or url or title),
                title=title,
                url=url,
                absolute_url=url,
                location_raw=loc_s,
                updated_at=parse_datetime(job.get("updated_at")),
                raw=job,
            )
        )
    return stubs


def stubs_from_rss(xml_text: str, base_url: str) -> list[JobStub]:
    """Parse a jobs RSS/Atom feed into stubs."""
    if not xml_text or "<" not in xml_text:
        return []
    tree = HTMLParser(xml_text)
    stubs: list[JobStub] = []
    for item in tree.css("item, entry"):
        title_node = item.css_first("title")
        link_node = item.css_first("link")
        title = _clean_title(title_node.text() if title_node else "")
        href = ""
        if link_node is not None:
            href = (link_node.attributes.get("href") or link_node.text() or "").strip()
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        if not _usable_title(title):
            title = _title_from_url(abs_url)
        if not _usable_title(title):
            continue
        stubs.append(
            JobStub(
                external_job_id=abs_url.rstrip("/"),
                title=title,
                url=abs_url,
                absolute_url=abs_url,
            )
        )
    return stubs


def listing_follow_urls(html: str, base_url: str, *, limit: int = 5) -> list[str]:
    """Find secondary listing hubs (View open roles, job-listing, RSS).

    Prefer explicit CTAs / cross-host job hubs from the page HTML, then a few
    same-origin guesses.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        raw = (url or "").strip()
        if not raw:
            return
        u = raw.split("#", 1)[0].rstrip("/")
        if not u or u in seen:
            return
        base_key = base_url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        if u.split("?", 1)[0].rstrip("/") == base_key:
            return
        seen.add(u)
        out.append(raw if raw.endswith(".xml") else u)

    if html:
        tree = HTMLParser(html)
        for a in tree.css("a[href], link[href]"):
            href = (a.attributes.get("href") or "").strip()
            if not href or href.startswith("mailto:"):
                continue
            abs_url = urljoin(base_url, href)
            text = _clean_title(a.text(separator=" ") if hasattr(a, "text") else "")
            low = abs_url.lower()
            if low.endswith(".xml") and "job" in low:
                _add(abs_url)
                continue
            if _LISTING_CTA.search(text or ""):
                _add(abs_url)
                continue
            if re.search(r"/[a-z]{2}(?:-[a-z]{2})?/company/careers", low):
                continue
            if any(
                token in low
                for token in (
                    "/job-listing",
                    "/job-listings",
                    "/open-roles",
                    "/open-positions",
                    "/current-openings",
                    "/work-with-us/jobs",
                    "/work-with-us/job/",
                    "/feed/jobs",
                )
            ):
                _add(abs_url)

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    if origin and len(out) < limit:
        for path in (
            "/jobs",
            "/open-roles",
            "/work-with-us/jobs",
            "/company/careers/job-listing",
            "/careers/jobs",
            "/feed/jobs.xml",
        ):
            _add(urljoin(origin + "/", path.lstrip("/")))
            if len(out) >= limit:
                break
    return out[:limit]


def discover_ats_boards(*texts: str) -> dict[str, set[str]]:
    """Pull Greenhouse/Lever/Ashby board slugs from HTML or XHR URLs."""
    found: dict[str, set[str]] = {"greenhouse": set(), "lever": set(), "ashby": set()}
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return found
    for m in _GH_BOARD.finditer(blob):
        slug = next((g for g in m.groups() if g), None)
        if slug and slug.lower() not in {"embed", "v1", "boards", "jobs", "api"}:
            found["greenhouse"].add(slug)
    for m in _LEVER_BOARD.finditer(blob):
        slug = m.group(1)
        if slug.lower() not in {"jobs", "api", "www"}:
            found["lever"].add(slug)
    for m in _ASHBY_BOARD.finditer(blob):
        slug = m.group(1)
        if slug.lower() not in {"api", "www"}:
            found["ashby"].add(slug)
    return found


async def scrape_careers_page(url: str, timeout_ms: int = 25000) -> list[JobStub]:
    """Playwright scrape: network JSON + DOM (+ light pagination clicks)."""
    from app.connectors.playwright_pool import scrape_with_browser

    result = await scrape_with_browser(url, timeout_ms=timeout_ms)
    return list(result or [])


def _stub_from_card(node: Any, base_url: str) -> JobStub | None:
    link = node.css_first("a[href]")
    href = None
    title = None
    if link is not None:
        href = link.attributes.get("href")
        title = _clean_title(link.text(separator=" ") or "")
    if not href:
        href = node.attributes.get("data-url") or node.attributes.get("href")
    if not title:
        for sel in ("h2", "h3", "h4", ".title", ".job-title", "[data-title]"):
            tnode = node.css_first(sel)
            if tnode:
                title = _clean_title(tnode.text(separator=" ") or "")
                if title:
                    break
    if not href or not title:
        return None
    abs_url = urljoin(base_url, href)
    if not _usable_title(title):
        return None
    if not _is_job_candidate_url(abs_url):
        return None
    return JobStub(
        external_job_id=abs_url.split("#", 1)[0].rstrip("/"),
        title=title,
        url=abs_url,
        absolute_url=abs_url,
    )


def _is_job_candidate_url(url: str) -> bool:
    if looks_like_job_url(url):
        return True
    if not _JOB_HREF.search(url):
        return False
    low = url.lower()
    # Reject bare careers marketing paths matched too loosely
    if any(
        bad in low
        for bad in (
            "/awards/",
            "/media_",
            "/about-bamboohr/",
            "/blog/",
            "/press/",
            "/news/",
        )
    ):
        return False
    path = urlparse(url).path.rstrip("/")
    last = path.rsplit("/", 1)[-1].lower()
    if last in {"careers", "career", "jobs", "job", "openings", "positions"}:
        return False
    # Query-id forms already handled by looks_like_job_url; keep greenhouse hosts
    if "greenhouse.io" in low or "lever.co" in low or "ashbyhq.com" in low:
        return True
    qs = parse_qs(urlparse(url).query)
    if any(k.lower() in {"gh_jid", "job_id", "jobid", "req_id"} for k in qs):
        return True
    return bool(re.search(r"/jobs?/[^/]+/.+", path, re.I) or re.search(r"/job/[^/]+", path, re.I))


def _usable_title(title: str) -> bool:
    if not title or len(title) < 4:
        return False
    if _BAD_TITLE.search(title):
        return False
    if title.lower() in {"view job", "apply", "open position"}:
        return False
    return is_tech_role(title)


def _find_job_arrays(data: Any, depth: int = 0) -> list[Any]:
    if depth > 6:
        return []
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            sample = data[0]
            if any(
                k in sample
                for k in (
                    "title",
                    "jobTitle",
                    "absolute_url",
                    "hostedUrl",
                    "jobUrl",
                    "requisitionId",
                )
            ):
                return data
        found: list[Any] = []
        for item in data[:50]:
            found.extend(_find_job_arrays(item, depth + 1))
        return found
    if isinstance(data, dict):
        for key in (
            "jobs",
            "jobPostings",
            "postings",
            "results",
            "data",
            "items",
            "positions",
            "openings",
            "vacancies",
            "jobList",
            "content",
        ):
            val = data.get(key)
            if isinstance(val, list) and val:
                hit = _find_job_arrays(val, depth + 1)
                if hit:
                    return hit
        for val in list(data.values())[:40]:
            hit = _find_job_arrays(val, depth + 1)
            if hit:
                return hit
    return []


def _location_from_job(job: dict[str, Any]) -> str | None:
    loc = job.get("location") or job.get("locations") or job.get("jobLocation")
    if isinstance(loc, list):
        return join_locations(*loc)
    if isinstance(loc, dict):
        return (
            loc.get("name")
            or loc.get("city")
            or loc.get("fullLocation")
            or join_locations(loc.get("city"), loc.get("state"), loc.get("country"))
        )
    if isinstance(loc, str):
        return loc
    return None


def _clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip(" ·|-")
    text = re.sub(
        r"\s+(Open\s+position|Apply|View\s+job|Remote|Hybrid|On[\s-]?site)(\s+.*)?$",
        "",
        text,
        flags=re.I,
    )
    # Drop department crumbs after the role when link text is multi-line
    text = re.sub(
        r"^(.*?)\s+(Engineering|Product|Infrastructure|Systems|Marketing|Legal|Sales|"
        r"Customer Success|Pressable|WooCommerce|Automattic.*)$",
        r"\1",
        text,
        flags=re.I,
    )
    if len(text) > 160:
        text = text[:160].rsplit(" ", 1)[0]
    return text.strip(" ·|-")


def _title_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1]
    slug = re.sub(r"[-_]+", " ", slug)
    slug = re.sub(r"\.(html?|php)$", "", slug, flags=re.I)
    return slug.strip().title()


def merge_stubs(*groups: list[JobStub]) -> list[JobStub]:
    out: dict[str, JobStub] = {}
    for group in groups:
        for stub in group:
            key = (stub.url or stub.external_job_id or stub.title).lower()
            if key and key not in out:
                out[key] = stub
    return list(out.values())
