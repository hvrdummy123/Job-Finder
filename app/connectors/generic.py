from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import extruct
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.connectors.page_scraper import merge_stubs, scrape_careers_page, stubs_from_html
from app.connectors.util import content_hash, fingerprint_ids, html_to_text, join_locations, parse_datetime
from app.http_client import HttpClient
from app.ingest.role_filter import is_tech_role, looks_like_job_url
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

log = logging.getLogger(__name__)

# Sitemap: require a job-like path segment, not just the word "career"
_SITEMAP_JOB_URL = re.compile(
    r"""
    /(jobs?|positions?|openings?|requisitions?)/
    [^/\s]{2,}
    |
    /job[-_]?detail
    |
    [?&](gh_jid|jobId|job_id|req_id)=
    """,
    re.I | re.X,
)

_JSON_API_HINT = re.compile(
    r"""["']([^"']*(?:/api/[^"']*jobs|/api/[^"']*postings|/boards-api/)[^"']*)["']""",
    re.I,
)


class GenericConnector:
    name = "generic"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        return DetectResult(
            ats_type=self.name,
            confidence=0.2,
            fallback_stage="unknown",
            needs_playwright=False,
        )

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        # Stage 1: JSON-LD JobPosting only (real structured jobs)
        try:
            page = await client.get_text(company.careers_url, respect_robots=True)
            stubs = _filter_stubs(_from_jsonld(page.text, page.final_url))
            if stubs:
                return ListResult(
                    stubs=stubs,
                    complete=True,
                    fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
                    fallback_stage="jsonld",
                    needs_playwright=False,
                )
        except PermissionError:
            return ListResult(
                stubs=[], complete=False, fallback_stage="jsonld", needs_playwright=True
            )
        except Exception as exc:
            log.warning("generic jsonld failed for %s: %s", company.name, exc)

        # Stage 2: strict sitemap job URLs (not marketing/career hubs)
        try:
            stubs = _filter_stubs(await _from_sitemap(company.careers_url, client))
            if stubs:
                return ListResult(
                    stubs=stubs,
                    complete=True,
                    fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
                    fallback_stage="sitemap",
                    needs_playwright=False,
                )
        except Exception as exc:
            log.warning("generic sitemap failed for %s: %s", company.name, exc)

        # Stage 3: discovered JSON job APIs embedded in the page
        try:
            stubs = _filter_stubs(await _from_discovered_api(company.careers_url, client))
            if stubs:
                return ListResult(
                    stubs=stubs,
                    complete=True,
                    fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
                    fallback_stage="xhr",
                    needs_playwright=False,
                )
        except Exception as exc:
            log.warning("generic xhr failed for %s: %s", company.name, exc)

        # Stage 4: static HTML job-link scrape (no public ATS API required)
        try:
            page = await client.get_text(company.careers_url, respect_robots=True)
            html_stubs = _filter_stubs(stubs_from_html(page.text, page.final_url))
            jsonld_stubs = _filter_stubs(_from_jsonld(page.text, page.final_url))
            stubs = merge_stubs(html_stubs, jsonld_stubs)
            if stubs:
                return ListResult(
                    stubs=stubs,
                    complete=True,
                    fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
                    fallback_stage="html",
                    needs_playwright=False,
                )
        except PermissionError:
            pass
        except Exception as exc:
            log.warning("generic html scrape failed for %s: %s", company.name, exc)

        # Stage 5: Playwright — capture XHR JSON + scrape rendered DOM
        try:
            scraped = await scrape_careers_page(company.careers_url)
            stubs = _filter_stubs(list(scraped or []))
            if stubs:
                return ListResult(
                    stubs=stubs,
                    complete=True,
                    fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
                    fallback_stage="playwright",
                    needs_playwright=False,
                )
        except ImportError:
            return ListResult(
                stubs=[],
                complete=False,
                fallback_stage="html",
                needs_playwright=True,
            )
        except Exception as exc:
            log.warning("playwright scrape failed for %s: %s", company.name, exc)

        return ListResult(
            stubs=[], complete=False, fallback_stage="playwright", needs_playwright=True
        )

    async def get_job(
        self,
        company: Company,
        source: Source,
        stub: JobStub,
        client: HttpClient,
    ) -> JobDraft:
        url = stub.url or stub.absolute_url
        html = ""
        text = ""
        raw: dict[str, Any] = dict(stub.raw or {})

        if url and looks_like_job_url(url):
            try:
                page = await client.get_text(url, respect_robots=True)
                html = page.text
                postings = _extract_jobpostings(html, page.final_url)
                if postings:
                    p = postings[0]
                    desc = p.get("description") or ""
                    html = desc if "<" in str(desc) else html
                    text = html_to_text(str(desc)) if desc else html_to_text(html)
                    raw = p
                else:
                    text = html_to_text(html)
            except Exception as exc:
                log.warning("generic get_job fetch failed %s: %s", url, exc)

        if not text:
            text = stub.title

        return JobDraft(
            external_job_id=stub.external_job_id,
            title=stub.title,
            url=url,
            apply_url=url,
            location_raw=stub.location_raw or _loc_from_raw(raw),
            description_html=html if html else None,
            description_text=text,
            content_hash=content_hash(text),
            posted_at=parse_datetime(raw.get("datePosted")),
            raw=raw,
        )


def _filter_stubs(stubs: list[JobStub]) -> list[JobStub]:
    """Keep only stubs that look like real tech job postings."""
    out: list[JobStub] = []
    for s in stubs:
        if not looks_like_job_url(s.url or s.absolute_url):
            continue
        if not is_tech_role(s.title):
            continue
        out.append(s)
    return out


def _from_jsonld(html: str, base_url: str) -> list[JobStub]:
    postings = _extract_jobpostings(html, base_url)
    stubs: list[JobStub] = []
    for i, p in enumerate(postings):
        url = p.get("url") or p.get("mainEntityOfPage") or None
        if isinstance(url, dict):
            url = url.get("@id") or url.get("url")
        title = p.get("title") or p.get("name") or ""
        if not title or not url:
            continue
        external_id = str(p.get("identifier") or p.get("@id") or url)
        if isinstance(p.get("identifier"), dict):
            external_id = str(p["identifier"].get("value") or external_id)
        stubs.append(
            JobStub(
                external_job_id=external_id,
                title=str(title),
                url=str(url),
                location_raw=_loc_from_raw(p),
                updated_at=parse_datetime(p.get("datePosted")),
                raw=p,
            )
        )
    return stubs


def _extract_jobpostings(html: str, base_url: str) -> list[dict[str, Any]]:
    postings: list[dict[str, Any]] = []
    try:
        data = extruct.extract(
            html,
            base_url=base_url,
            syntaxes=["json-ld"],
            uniform=True,
        )
        for item in data.get("json-ld") or []:
            postings.extend(_walk_jobposting(item))
    except Exception:
        tree = HTMLParser(html)
        for node in tree.css('script[type="application/ld+json"]'):
            try:
                payload = json.loads(node.text())
            except Exception:
                continue
            postings.extend(_walk_jobposting(payload))
    return postings


def _walk_jobposting(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_walk_jobposting(item))
        return found
    if not isinstance(node, dict):
        return found
    types = node.get("@type")
    type_list = types if isinstance(types, list) else [types]
    if any(t and "JobPosting" in str(t) for t in type_list):
        found.append(node)
    for key in ("@graph", "itemListElement", "mainEntity"):
        if key in node:
            found.extend(_walk_jobposting(node[key]))
    return found


async def _from_sitemap(careers_url: str, client: HttpClient) -> list[JobStub]:
    parsed = urlparse(careers_url)
    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    resp = await client.request("GET", sitemap_url, respect_robots=True)
    if resp.status_code >= 400:
        return []
    urls = re.findall(r"<loc>\s*([^<]+)\s*</loc>", resp.text)
    job_urls = [u.strip() for u in urls if _SITEMAP_JOB_URL.search(u) and looks_like_job_url(u)]
    job_urls = job_urls[:150]
    stubs: list[JobStub] = []
    for u in job_urls:
        slug = u.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")
        title = slug.title()
        stubs.append(JobStub(external_job_id=u, title=title, url=u))
    return stubs


async def _from_discovered_api(careers_url: str, client: HttpClient) -> list[JobStub]:
    page = await client.get_text(careers_url, respect_robots=True)
    candidates: list[str] = []
    for match in _JSON_API_HINT.finditer(page.text):
        path = match.group(1)
        if path.startswith("http") or path.startswith("/"):
            candidates.append(urljoin(page.final_url, path))
    seen: set[str] = set()
    stubs: list[JobStub] = []
    for api_url in candidates:
        if api_url in seen:
            continue
        seen.add(api_url)
        if len(seen) > 10:
            break
        try:
            resp = await client.request("GET", api_url)
            if resp.status_code >= 400:
                continue
            ctype = resp.headers.get("content-type", "")
            if "json" not in ctype and not resp.text.strip().startswith(("{", "[")):
                continue
            data = resp.json()
            extracted = _stubs_from_unknown_json(data, page.final_url)
            if extracted:
                stubs = extracted
                break
        except Exception:
            continue
    return stubs


def _stubs_from_unknown_json(data: Any, base_url: str) -> list[JobStub]:
    jobs: list[Any] = []
    if isinstance(data, list):
        jobs = data
    elif isinstance(data, dict):
        for key in ("jobs", "jobPostings", "postings", "results", "data", "items"):
            if isinstance(data.get(key), list):
                jobs = data[key]
                break
    stubs: list[JobStub] = []
    for i, job in enumerate(jobs):
        if not isinstance(job, dict):
            continue
        title = job.get("title") or job.get("name") or job.get("text")
        if not title:
            continue
        job_id = str(job.get("id") or job.get("uuid") or job.get("slug") or job.get("url") or i)
        url = job.get("url") or job.get("absolute_url") or job.get("hostedUrl") or job.get("jobUrl")
        if url and isinstance(url, str) and url.startswith("/"):
            url = urljoin(base_url, url)
        stubs.append(
            JobStub(
                external_job_id=job_id,
                title=str(title),
                url=str(url) if url else None,
                location_raw=_loc_from_raw(job),
                updated_at=parse_datetime(job.get("updated_at") or job.get("createdAt") or job.get("posted_date")),
                raw=job,
            )
        )
    return stubs


def _loc_from_raw(raw: dict[str, Any]) -> str | None:
    loc = raw.get("jobLocation") or raw.get("location")
    if isinstance(loc, list) and loc:
        return join_locations(*[_single_loc(item) for item in loc])
    return _single_loc(loc)


def _single_loc(loc: Any) -> str | None:
    if isinstance(loc, str):
        return loc.strip() or None
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts = [
                addr.get("addressLocality"),
                addr.get("addressRegion"),
                addr.get("addressCountry"),
            ]
            joined = ", ".join(str(p) for p in parts if p)
            return joined or None
        return loc.get("name") or loc.get("addressLocality")
    return None
