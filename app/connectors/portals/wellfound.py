from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.connectors.util import content_hash, fingerprint_ids, html_to_text, parse_datetime
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

# Wellfound role search pages (public HTML + embedded JSON when present).
# Example: https://wellfound.com/role/l/software-engineer/remote

_ROLE_MAP = {
    "remote-software-engineer": "https://wellfound.com/role/l/software-engineer/remote",
    "remote-backend": "https://wellfound.com/role/l/backend-engineer/remote",
    "remote-blockchain": "https://wellfound.com/role/l/blockchain/remote",
    "remote-fullstack": "https://wellfound.com/role/l/full-stack-engineer/remote",
}


class WellfoundConnector:
    name = "wellfound"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        # URL-only — many careers pages link to Wellfound in footers and must not
        # steal detection from Greenhouse/Lever/generic.
        u = (url or "").lower()
        if u.startswith("portal://wellfound") or "wellfound.com" in u or "angel.co" in u:
            return DetectResult(ats_type=self.name, confidence=0.85, fallback_stage="xhr")
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        slug = source.ats_slug or "remote-software-engineer"
        list_url = _ROLE_MAP.get(slug, f"https://wellfound.com/role/l/{slug}/remote")
        if slug.startswith("http"):
            list_url = slug

        try:
            page = await client.get_text(list_url, respect_robots=True)
        except PermissionError:
            return ListResult(
                stubs=[], complete=False, fallback_stage="jsonld", needs_playwright=True
            )
        if page.status_code >= 400:
            return ListResult(stubs=[], complete=False, fallback_stage="xhr")

        stubs = _extract_from_html(page.text, page.final_url)
        if not stubs:
            # Playwright fallback for JS-heavy pages
            try:
                from app.connectors.playwright_pool import fetch_rendered_html

                html = await fetch_rendered_html(list_url)
                if html:
                    stubs = _extract_from_html(html, list_url)
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

        employer = stub.raw.get("employer")
        return JobDraft(
            external_job_id=stub.external_job_id,
            title=stub.title,
            url=stub.url,
            apply_url=apply_url,
            location_raw=stub.location_raw or "Remote",
            workplace_type="remote",
            department=employer,
            description_html=html or None,
            description_text=text,
            content_hash=content_hash(text),
            posted_at=stub.updated_at,
            raw={**stub.raw, "portal": "wellfound"},
        )


def _extract_from_html(html: str, base_url: str) -> list[JobStub]:
    stubs: list[JobStub] = []
    # Next.js / Apollo embedded state
    for pattern in (
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        r'window\.__APOLLO_STATE__\s*=\s*(\{.*?\});\s*</script>',
    ):
        m = re.search(pattern, html, re.S)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
            stubs.extend(_walk_jobs(data, base_url))
            if stubs:
                return stubs
        except json.JSONDecodeError:
            continue

    # Anchor fallback: job links
    tree = HTMLParser(html)
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if "/jobs/" not in href and "/job/" not in href:
            continue
        title = (a.text() or "").strip()
        if len(title) < 3:
            continue
        url = urljoin(base_url, href)
        external_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        stubs.append(
            JobStub(
                external_job_id=external_id,
                title=title,
                url=url,
                location_raw="Remote",
                raw={"portal": "wellfound", "href": href},
            )
        )
    # Dedupe by url
    seen: set[str] = set()
    unique: list[JobStub] = []
    for s in stubs:
        if s.url in seen:
            continue
        seen.add(s.url or s.external_job_id)
        unique.append(s)
    return unique[:100]


def _walk_jobs(node: Any, base_url: str) -> list[JobStub]:
    found: list[JobStub] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_walk_jobs(item, base_url))
        return found
    if not isinstance(node, dict):
        return found

    title = node.get("title") or node.get("name")
    slug = node.get("slug") or node.get("id")
    company = None
    startup = node.get("startup") or node.get("company") or {}
    if isinstance(startup, dict):
        company = startup.get("name") or startup.get("companyName")

    looks_like_job = bool(title) and (
        "job" in str(node.get("__typename", "")).lower()
        or node.get("remote") is not None
        or "startup" in node
        or node.get("primaryRoleTitle")
    )
    if looks_like_job and title:
        path = node.get("slug") or node.get("id") or ""
        url = node.get("url") or node.get("jobUrl")
        if not url and path:
            url = urljoin(base_url, f"/jobs/{path}")
        external = str(node.get("id") or url or title)
        external_id = hashlib.sha256(external.encode("utf-8")).hexdigest()[:24]
        found.append(
            JobStub(
                external_job_id=external_id,
                title=str(title),
                url=str(url) if url else None,
                location_raw="Remote" if node.get("remote") else node.get("location"),
                raw={"portal": "wellfound", "employer": company, "node": _small(node)},
            )
        )

    for value in node.values():
        if isinstance(value, (dict, list)):
            found.extend(_walk_jobs(value, base_url))
    return found


def _small(node: dict[str, Any]) -> dict[str, Any]:
    skip = {"description", "descriptionHtml", "content"}
    return {k: v for k, v in node.items() if k not in skip and not isinstance(v, (dict, list))}
