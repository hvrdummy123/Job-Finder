from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from app.connectors import registry
from app.connectors.oracle import _pack_slug, _parse_slug_parts
from app.http_client import HttpClient
from app.models import DetectResult, FetchResult

log = logging.getLogger(__name__)

_SUBPATHS = (
    "/jobs",
    "/careers/jobs",
    "/open-roles",
    "/careers/openings",
    "/en/sites/CX_1",
    "/sites/CX_1",
)


async def detect_ats(
    careers_url: str,
    client: HttpClient,
    company_name: str | None = None,
) -> DetectResult:
    """Fingerprint ATS for any company from careers URL + name.

    Order: HTML fingerprint → Oracle CE discovery → public board slug probe →
    common job subpaths. No per-company special cases required for new adds.
    """
    if careers_url.startswith("portal://"):
        return registry.detect(careers_url, None)

    response = await _fetch_html(careers_url, client)
    final_url = response.final_url if response else careers_url
    # Prefer the original careers URL for slug extraction — redirects often
    # strip the board path (e.g. SmartRecruiters → jobs.smartrecruiters.com).
    result = registry.detect(careers_url, response)
    if response is not None:
        alt = registry.detect(final_url, response)
        result = _prefer_detect(result, alt)

    # Oracle Recruiting Cloud: discover host+site from redirect/HTML (any tenant)
    if result.ats_type in {"generic", "unknown"} or (
        result.ats_type == "oracle" and not result.ats_slug
    ):
        oracle = await _discover_oracle(careers_url, client, response, company_name)
        if oracle and oracle.confidence >= result.confidence:
            result = oracle

    # Pull board IDs embedded in HTML (SmartRecruiters/Greenhouse/…) even on SPAs
    if response and (
        result.ats_type in {"generic", "unknown"} or not result.ats_slug
    ):
        embedded = _detect_embedded_boards(response.text or "", careers_url)
        if embedded:
            result = _prefer_detect(result, embedded)

    # Probe public ATS APIs with slug candidates from name/host
    if result.ats_type in {"generic", "unknown"} or (
        result.ats_type in {"greenhouse", "lever", "ashby", "smartrecruiters"}
        and not result.ats_slug
    ):
        probed = await _probe_public_boards(careers_url, client, company_name)
        if probed and probed.confidence >= result.confidence:
            result = probed

    # SPA marketing pages: crawl a few job subpaths
    if result.ats_type in {"generic", "unknown"} or (
        result.confidence < 0.8 and not result.ats_slug
    ):
        crawled = await _detect_via_subpaths(careers_url, client)
        if crawled and crawled.confidence > result.confidence:
            result = crawled

    return result


def _detect_embedded_boards(text: str, careers_url: str) -> DetectResult | None:
    """Extract ATS board slugs from page HTML for any company."""
    if not text:
        return None
    m = re.search(
        r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]{2,60})",
        text,
        re.I,
    )
    if m and m.group(1).lower() not in _RESERVED_SLUGS:
        return DetectResult(
            ats_type="smartrecruiters",
            ats_slug=m.group(1),
            confidence=0.9,
            fallback_stage="api",
        )
    m = (
        re.search(
            r"boards-api(?:\.eu)?\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]{2,60})",
            text,
            re.I,
        )
        or re.search(
            r"job-boards(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]{2,60})",
            text,
            re.I,
        )
        or re.search(
            r"boards(?:-api)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]{2,60})",
            text,
            re.I,
        )
        or re.search(
            r"greenhouse\.io/[^?\s\"']*[?&]for=([A-Za-z0-9_-]{2,60})",
            text,
            re.I,
        )
    )
    if m and m.group(1).lower() not in _RESERVED_SLUGS:
        return DetectResult(
            ats_type="greenhouse",
            ats_slug=m.group(1),
            confidence=0.9,
            fallback_stage="api",
        )
    m = re.search(r"(?:jobs\.)?lever\.co/([A-Za-z0-9_-]{2,60})", text, re.I)
    if m and m.group(1).lower() not in _RESERVED_SLUGS:
        return DetectResult(
            ats_type="lever",
            ats_slug=m.group(1),
            confidence=0.9,
            fallback_stage="api",
        )
    m = re.search(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]{2,60})", text, re.I)
    if m and m.group(1).lower() not in _RESERVED_SLUGS:
        return DetectResult(
            ats_type="ashby",
            ats_slug=m.group(1),
            confidence=0.9,
            fallback_stage="api",
        )
    # Workday tenant/site in absolute links
    m = re.search(
        r"([a-z0-9_-]+)\.(?:wd\d+\.)?myworkdayjobs\.com/(?:[a-z]{2}-[a-z]{2}/)?([A-Za-z0-9_-]+)",
        text,
        re.I,
    )
    if m:
        return DetectResult(
            ats_type="workday",
            ats_slug=f"{m.group(1)}/{m.group(2)}",
            confidence=0.9,
            fallback_stage="api",
        )
    return None


async def _fetch_html(url: str, client: HttpClient) -> FetchResult | None:
    try:
        return await client.get_text(url, respect_robots=True)
    except PermissionError:
        log.warning("robots.txt blocks careers page fetch: %s", url)
        return None
    except Exception as exc:
        log.warning("detect fetch failed for %s: %s", url, exc)
        return None


async def _discover_oracle(
    careers_url: str,
    client: HttpClient,
    response: FetchResult | None,
    company_name: str | None,
) -> DetectResult | None:
    """Find Oracle CE tenant+site from page HTML/redirects (not company-specific)."""
    texts: list[str] = [careers_url]
    if response:
        texts.append(response.final_url or "")
        texts.append(response.text or "")

    # Follow common Oracle Candidate Experience paths
    parsed = urlparse(careers_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for path in ("/en/sites/CX_1", "/sites/CX_1", "/hcmUI/CandidateExperience/en/sites/CX_1"):
        probe_url = origin + path
        if response and response.final_url and path in response.final_url:
            continue
        extra = await _fetch_html(probe_url, client)
        if extra and extra.status_code < 400:
            texts.append(extra.final_url or "")
            texts.append(extra.text or "")
            hit = _parse_slug_parts(extra.final_url, extra.text)
            if hit:
                host, site, portal = hit
                return DetectResult(
                    ats_type="oracle",
                    ats_slug=_pack_slug(host, site, portal or extra.final_url or careers_url),
                    confidence=0.93,
                    fallback_stage="api",
                )

    blob = "\n".join(texts)
    hit = _parse_slug_parts(careers_url, blob)
    if hit:
        host, site, portal = hit
        return DetectResult(
            ats_type="oracle",
            ats_slug=_pack_slug(host, site, portal or careers_url),
            confidence=0.9,
            fallback_stage="api",
        )

    # Host-only signal in HTML without site — default CX_1 (Oracle CE convention)
    host_m = re.search(r"([a-z0-9.-]+\.fa\.[a-z0-9.-]+\.oraclecloud\.com)", blob, re.I)
    if host_m:
        host = host_m.group(1)
        portal = careers_url
        for m in re.finditer(
            r"https?://(?![\w.-]*oraclecloud)[\w.-]+(?:/[^\"'\s]*)?/sites/CX_\d+",
            blob,
            re.I,
        ):
            portal = m.group(0).split("/sites/")[0]
            break
        return DetectResult(
            ats_type="oracle",
            ats_slug=_pack_slug(host, "CX_1", portal),
            confidence=0.88,
            fallback_stage="api",
        )
    return None


async def _detect_via_subpaths(careers_url: str, client: HttpClient) -> DetectResult | None:
    best: DetectResult | None = None
    base = careers_url if careers_url.endswith("/") else careers_url + "/"
    parsed = urlparse(careers_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates: list[str] = []
    for path in _SUBPATHS:
        candidates.append(urljoin(base, path.lstrip("/")))
        candidates.append(origin + path)
    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        resp = await _fetch_html(url, client)
        if not resp or resp.status_code >= 400:
            continue
        hit = registry.detect(resp.final_url, resp)
        if hit.ats_type == "generic":
            # Still try Oracle discovery on this page
            oracle = await _discover_oracle(url, client, resp, None)
            if oracle:
                hit = oracle
            else:
                continue
        if best is None or hit.confidence > best.confidence:
            best = hit
        if best.confidence >= 0.9 and best.ats_slug:
            break
    return best


_RESERVED_SLUGS = {
    "www",
    "careers",
    "jobs",
    "apply",
    "com",
    "co",
    "io",
    "ai",
    "net",
    "org",
    "lifeat",
    "example",
    "test",
    "demo",
    "sample",
    "localhost",
    "greenhouse",
    "lever",
    "ashby",
    "ashbyhq",
    "smartrecruiters",
    "workday",
    "myworkdayjobs",
    "oraclecloud",
    "amazon",
    "indeed",
    "wellfound",
    "naukri",
}


def _prefer_detect(a: DetectResult, b: DetectResult) -> DetectResult:
    """Keep the stronger ATS hit; prefer one that already has a board slug."""
    if a.ats_type == "generic" and b.ats_type != "generic":
        return b
    if b.ats_type == "generic" and a.ats_type != "generic":
        return a
    if (a.ats_slug and not b.ats_slug) and a.ats_type == b.ats_type:
        return a
    if (b.ats_slug and not a.ats_slug) and a.ats_type == b.ats_type:
        return b
    if b.confidence > a.confidence:
        return b
    if a.confidence > b.confidence:
        return a
    # tie: prefer slug-bearing
    if b.ats_slug and not a.ats_slug:
        return b
    return a


def _slug_candidates(careers_url: str, company_name: str | None = None) -> list[str]:
    parsed = urlparse(careers_url)
    host = (parsed.netloc or "").lower().split(":")[0]
    labels = [
        p
        for p in host.split(".")
        if p and p not in _RESERVED_SLUGS
    ]
    out: list[str] = []
    if company_name:
        raw = company_name.strip()
        out.append(raw)
        out.append(re.sub(r"[^a-zA-Z0-9]+", "", raw))
        out.append(re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-"))
        out.append(re.sub(r"[^a-z0-9]+", "", raw.lower()))
        cleaned = re.sub(
            r"\b(inc|llc|corp|co|ltd|limited|technologies|technology|labs|software)\b",
            "",
            raw,
            flags=re.I,
        )
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "", cleaned)
        if cleaned:
            out.append(cleaned)
            out.append(cleaned.lower())
            # Title-case compact form helps SmartRecruiters (Canva, Stripe, …)
            if cleaned.lower() != cleaned:
                out.append(cleaned[0].upper() + cleaned[1:])
            else:
                out.append(cleaned[:1].upper() + cleaned[1:].lower())
    for label in labels[:3]:
        out.append(label)
        out.append(label.capitalize())
        out.append(re.sub(r"[^a-z0-9]+", "", label))
    _path_noise = {
        "careers",
        "career",
        "jobs",
        "job",
        "openings",
        "opening",
        "positions",
        "en",
        "en-us",
        "en-gb",
        "us",
        "uk",
        "in",
        "company",
        "about",
        "teams",
        "search",
        "home",
        "index",
        "join",
        "work-with-us",
        "open-roles",
        "all-jobs",
        "sites",
        "cx_1",
        "cx_2",
    }
    for part in parsed.path.split("/"):
        part = part.strip()
        if (
            part
            and re.fullmatch(r"[A-Za-z0-9_-]{2,40}", part)
            and part.lower() not in _path_noise
        ):
            out.append(part)
            out.append(part.lower())
    seen: set[str] = set()
    ordered: list[str] = []
    for s in out:
        key = s.lower()
        if not s or key in seen or len(s) < 2 or key in _path_noise or key in _RESERVED_SLUGS:
            continue
        seen.add(key)
        ordered.append(s)
    return ordered[:12]


def _jobs_look_real(jobs: list, slug: str) -> bool:
    """Reject demo/public sample boards that accept arbitrary slugs."""
    if not jobs:
        return False
    if slug.lower() in _RESERVED_SLUGS:
        return False
    return True


async def _probe_public_boards(
    careers_url: str,
    client: HttpClient,
    company_name: str | None = None,
) -> DetectResult | None:
    for slug in _slug_candidates(careers_url, company_name):
        try:
            resp = await client.request(
                "GET",
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                params={"content": "false"},
            )
            if resp.status_code < 400:
                data = resp.json()
                jobs = data.get("jobs") if isinstance(data, dict) else data
                if isinstance(jobs, list) and _jobs_look_real(jobs, slug):
                    return DetectResult(
                        ats_type="greenhouse",
                        ats_slug=slug,
                        confidence=0.92,
                        fallback_stage="api",
                    )
        except Exception:
            pass

        try:
            resp = await client.request(
                "GET",
                f"https://api.lever.co/v0/postings/{slug}",
                params={"mode": "json"},
            )
            if resp.status_code < 400:
                data = resp.json()
                if isinstance(data, list) and data:
                    return DetectResult(
                        ats_type="lever",
                        ats_slug=slug,
                        confidence=0.92,
                        fallback_stage="api",
                    )
        except Exception:
            pass

        try:
            resp = await client.request(
                "GET",
                f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            )
            if resp.status_code < 400:
                data = resp.json()
                jobs = data.get("jobs") if isinstance(data, dict) else None
                if isinstance(jobs, list) and jobs:
                    return DetectResult(
                        ats_type="ashby",
                        ats_slug=slug,
                        confidence=0.92,
                        fallback_stage="api",
                    )
        except Exception:
            pass

        try:
            resp = await client.request(
                "GET",
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                params={"limit": 1, "offset": 0},
            )
            if resp.status_code < 400:
                data = resp.json()
                if int(data.get("totalFound") or 0) > 0 or (data.get("content") or []):
                    return DetectResult(
                        ats_type="smartrecruiters",
                        ats_slug=slug,
                        confidence=0.92,
                        fallback_stage="api",
                    )
        except Exception:
            pass

    return None
