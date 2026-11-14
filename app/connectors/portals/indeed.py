from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlencode

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.connectors.util import content_hash, fingerprint_ids, html_to_text, parse_datetime
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

log = logging.getLogger(__name__)

# Indeed historically exposed RSS search feeds; when blocked, fall back to HTML/Playwright.
# Example: https://www.indeed.com/rss?q=software+engineer&l=Remote


class IndeedConnector:
    name = "indeed"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        u = (url or "").lower()
        if u.startswith("portal://indeed") or "indeed.com" in u:
            return DetectResult(ats_type=self.name, confidence=0.9, fallback_stage="api")
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        host, params = _parse_slug(source.ats_slug or "", company.careers_url)
        q = params.get("q", ["software engineer"])[0]
        loc = params.get("l", ["Remote"])[0]
        rss_url = f"https://{host}/rss?{urlencode({'q': q, 'l': loc})}"
        search_url = f"https://{host}/jobs?{urlencode({'q': q, 'l': loc, 'sort': 'date'})}"

        # 1) RSS
        try:
            resp = await client.request("GET", rss_url)
            if resp.status_code < 400 and "<item>" in (resp.text or "").lower():
                stubs = _parse_rss(resp.text, host)
                if stubs:
                    return ListResult(
                        stubs=stubs,
                        complete=True,
                        fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
                        fallback_stage="api",
                        needs_playwright=False,
                    )
        except Exception as exc:
            log.warning("indeed rss failed: %s", exc)

        # 2) Plain HTML search page
        try:
            page = await client.get_text(search_url, respect_robots=True)
            if page.status_code < 400:
                stubs = _parse_search_html(page.text, host)
                if stubs:
                    return ListResult(
                        stubs=stubs,
                        complete=True,
                        fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
                        fallback_stage="html",
                        needs_playwright=False,
                    )
        except Exception as exc:
            log.warning("indeed html failed: %s", exc)

        # 3) Playwright
        try:
            from app.connectors.playwright_pool import fetch_rendered_html

            html = await fetch_rendered_html(search_url)
            if html:
                stubs = _parse_search_html(html, host)
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
                stubs=[], complete=False, fallback_stage="html", needs_playwright=True
            )
        except Exception as exc:
            log.warning("indeed playwright failed: %s", exc)

        return ListResult(stubs=[], complete=False, fallback_stage="api", needs_playwright=True)

    async def get_job(
        self,
        company: Company,
        source: Source,
        stub: JobStub,
        client: HttpClient,
    ) -> JobDraft:
        # Prefer RSS description; optionally fetch the job page for more text.
        html = ""
        text = html_to_text(str(stub.raw.get("description") or ""))
        apply_url = stub.url
        if stub.url:
            try:
                page = await client.get_text(stub.url, respect_robots=True)
                if page.status_code < 400:
                    html = page.text
                    page_text = html_to_text(html)
                    if len(page_text) > len(text):
                        text = page_text
                    # Keep the Indeed listing URL as the apply target
                    apply_url = str(page.final_url or stub.url)
            except Exception:
                pass

        return JobDraft(
            external_job_id=stub.external_job_id,
            title=stub.title,
            url=stub.url,
            apply_url=apply_url,
            location_raw=stub.location_raw or "Remote",
            workplace_type="remote",
            description_html=html or stub.raw.get("description"),
            description_text=text or stub.title,
            content_hash=content_hash(text or stub.title),
            posted_at=stub.updated_at,
            raw={**stub.raw, "portal": "indeed"},
        )


def _parse_slug(slug: str, careers_url: str) -> tuple[str, dict[str, list[str]]]:
    host = "www.indeed.com"
    query = slug
    if careers_url.startswith("portal://indeed-in"):
        host = "in.indeed.com"
    if "host=" in slug:
        qs = parse_qs(slug)
        host = qs.get("host", [host])[0]
        query = urlencode({k: v[0] for k, v in qs.items() if k != "host"})
    elif slug.startswith("q=") or "&" in slug:
        query = slug
    else:
        # treat as free-text query
        query = urlencode({"q": slug.replace("-", " "), "l": "Remote"})
    return host, parse_qs(query)


def _parse_search_html(html: str, host: str) -> list[JobStub]:
    """Best-effort parse of Indeed search result cards / jk links."""
    stubs: list[JobStub] = []
    seen: set[str] = set()
    tree = HTMLParser(html)
    for a in tree.css("a[href*='jk='], a[href*='/viewjob'], a.jcs-JobTitle, h2.jobTitle a"):
        href = a.attributes.get("href") or ""
        title = (a.text() or "").strip()
        if not title or len(title) < 3:
            continue
        if href.startswith("/"):
            href = f"https://{host}{href}"
        m = re.search(r"[?&]jk=([a-zA-Z0-9]+)", href)
        jk = m.group(1) if m else href
        if jk in seen:
            continue
        seen.add(jk)
        stubs.append(
            JobStub(
                external_job_id=hashlib.sha256(jk.encode()).hexdigest()[:24],
                title=title,
                url=href,
                location_raw="Remote",
                raw={"portal": "indeed", "host": host, "jk": jk},
            )
        )
    if stubs:
        return stubs

    # Regex fallback when markup is obfuscated
    for m in re.finditer(
        r'href="([^"]*[?&]jk=([a-zA-Z0-9]+)[^"]*)"[^>]*>\s*([^<]{3,120})',
        html,
        re.I,
    ):
        href, jk, title = m.group(1), m.group(2), m.group(3).strip()
        if jk in seen or not title:
            continue
        seen.add(jk)
        if href.startswith("/"):
            href = f"https://{host}{href}"
        stubs.append(
            JobStub(
                external_job_id=hashlib.sha256(jk.encode()).hexdigest()[:24],
                title=html_to_text(title) or title,
                url=href,
                location_raw="Remote",
                raw={"portal": "indeed", "host": host, "jk": jk},
            )
        )
    return stubs


def _parse_rss(xml_text: str, host: str) -> list[JobStub]:
    stubs: list[JobStub] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall("item")
    for item in items:
        title = (item.findtext("title") or "Untitled").strip()
        link = (item.findtext("link") or "").strip()
        desc = item.findtext("description") or ""
        pub = item.findtext("pubDate")
        guid = (item.findtext("guid") or link or title).strip()
        # Extract company from "Title - Company" Indeed RSS pattern
        company_name = None
        if " - " in title:
            parts = title.rsplit(" - ", 1)
            if len(parts) == 2:
                title, company_name = parts[0].strip(), parts[1].strip()

        external_id = hashlib.sha256(guid.encode("utf-8")).hexdigest()[:24]
        stubs.append(
            JobStub(
                external_job_id=external_id,
                title=title,
                url=link,
                location_raw="Remote",
                updated_at=parse_datetime(pub) if pub else None,
                raw={
                    "description": desc,
                    "guid": guid,
                    "employer": company_name,
                    "portal": "indeed",
                    "host": host,
                },
            )
        )
    return stubs
