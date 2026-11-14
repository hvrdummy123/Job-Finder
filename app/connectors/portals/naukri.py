from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.connectors.util import content_hash, fingerprint_ids, html_to_text
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

# Naukri remote search pages. Prefer structured JSON embedded in the page.
# Example: https://www.naukri.com/remote-jobs?k=software%20engineer


class NaukriConnector:
    name = "naukri"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        u = (url or "").lower()
        if u.startswith("portal://naukri") or "naukri.com" in u:
            return DetectResult(ats_type=self.name, confidence=0.85, fallback_stage="xhr")
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        slug = source.ats_slug or "remote-jobs?k=software%20engineer"
        if slug.startswith("http"):
            list_url = slug
        else:
            list_url = f"https://www.naukri.com/{slug.lstrip('/')}"

        try:
            page = await client.get_text(list_url, respect_robots=True)
        except PermissionError:
            return ListResult(
                stubs=[], complete=False, fallback_stage="jsonld", needs_playwright=True
            )
        if page.status_code >= 400:
            return ListResult(stubs=[], complete=False, fallback_stage="xhr")

        stubs = _extract(page.text, page.final_url)
        if not stubs:
            try:
                from app.connectors.playwright_pool import fetch_rendered_html

                html = await fetch_rendered_html(list_url)
                if html:
                    stubs = _extract(html, list_url)
            except ImportError:
                return ListResult(
                    stubs=[],
                    complete=False,
                    fallback_stage="xhr",
                    needs_playwright=True,
                )
            except Exception:
                return ListResult(stubs=[], complete=False, fallback_stage="playwright")

        if not stubs:
            return ListResult(
                stubs=[], complete=False, fallback_stage="xhr", needs_playwright=True
            )

        return ListResult(
            stubs=stubs,
            complete=True,
            fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
            fallback_stage="xhr",
            needs_playwright=False,
        )

    async def get_job(
        self,
        company: Company,
        source: Source,
        stub: JobStub,
        client: HttpClient,
    ) -> JobDraft:
        text = stub.title
        html = ""
        apply_url = stub.url
        if stub.url:
            try:
                page = await client.get_text(stub.url, respect_robots=True)
                if page.status_code < 400:
                    html = page.text
                    text = html_to_text(html) or text
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
            department=stub.raw.get("employer"),
            description_html=html or None,
            description_text=text,
            content_hash=content_hash(text),
            raw={**stub.raw, "portal": "naukri"},
        )


def _extract(html: str, base_url: str) -> list[JobStub]:
    stubs: list[JobStub] = []

    # Naukri often embeds job data in script tags / state
    for pattern in (
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>",
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        r'"jobDetails"\s*:\s*(\[.*?\])\s*[,}]',
    ):
        m = re.search(pattern, html, re.S)
        if not m:
            continue
        try:
            payload = json.loads(m.group(1))
            stubs.extend(_walk(payload, base_url))
            if stubs:
                return stubs[:80]
        except json.JSONDecodeError:
            continue

    tree = HTMLParser(html)
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if "job-listings" not in href and "/job/" not in href:
            continue
        title = (a.text() or "").strip()
        if len(title) < 4:
            continue
        url = urljoin(base_url, href)
        external_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        stubs.append(
            JobStub(
                external_job_id=external_id,
                title=re.sub(r"\s+", " ", title),
                url=url,
                location_raw="Remote",
                raw={"portal": "naukri"},
            )
        )

    seen: set[str] = set()
    unique: list[JobStub] = []
    for s in stubs:
        key = s.url or s.external_job_id
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    return unique[:80]


def _walk(node: Any, base_url: str) -> list[JobStub]:
    found: list[JobStub] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_walk(item, base_url))
        return found
    if not isinstance(node, dict):
        return found

    title = node.get("title") or node.get("jobTitle") or node.get("designation")
    job_id = node.get("jobId") or node.get("id") or node.get("jobIdEncrypted")
    url = node.get("jdURL") or node.get("jobUrl") or node.get("url")
    company_name = node.get("companyName") or node.get("company") or node.get("employer")
    location = node.get("placeholders") or node.get("location") or node.get("jobLocation")
    if isinstance(location, list):
        location = ", ".join(str(x) for x in location)
    if isinstance(company_name, dict):
        company_name = company_name.get("name")

    if title and (job_id or url):
        if url and isinstance(url, str) and url.startswith("/"):
            url = urljoin("https://www.naukri.com", url)
        external = str(job_id or url or title)
        external_id = hashlib.sha256(external.encode("utf-8")).hexdigest()[:24]
        found.append(
            JobStub(
                external_job_id=external_id,
                title=str(title),
                url=str(url) if url else None,
                location_raw=str(location) if location else "Remote",
                raw={"portal": "naukri", "employer": company_name, "id": job_id},
            )
        )

    for value in node.values():
        if isinstance(value, (dict, list)):
            found.extend(_walk(value, base_url))
    return found
