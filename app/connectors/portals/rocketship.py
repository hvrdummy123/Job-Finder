"""Remote Rocketship portal — public title pages with embedded Next.js JSON."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any
from app.connectors.util import content_hash, fingerprint_ids, parse_datetime
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

log = logging.getLogger(__name__)

_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
    re.S,
)

# Default title slugs when ats_slug is a short alias
_SLUG_MAP = {
    "software-engineer": "software-engineer",
    "remote-software-engineer": "software-engineer",
    "backend": "backend-software-engineer",
    "remote-backend": "backend-software-engineer",
    "frontend": "frontend-software-engineer",
    "fullstack": "full-stack-software-engineer",
    "blockchain": "blockchain-developer",
    "data-engineer": "data-engineer",
    "ml-engineer": "machine-learning-engineer",
}


class RocketshipConnector:
    name = "rocketship"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        u = (url or "").lower()
        if u.startswith("portal://rocketship") or "remoterocketship.com" in u:
            return DetectResult(ats_type=self.name, confidence=0.9, fallback_stage="xhr")
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        slugs = _resolve_slugs(source.ats_slug)
        stubs: list[JobStub] = []
        seen: set[str] = set()

        for slug in slugs:
            list_url = f"https://www.remoterocketship.com/jobs/{slug.strip('/')}/"
            try:
                page = await client.get_text(list_url, respect_robots=True)
            except PermissionError:
                continue
            except Exception as exc:
                log.warning("rocketship fetch failed %s: %s", list_url, exc)
                continue
            if page.status_code >= 400:
                continue

            for stub in _stubs_from_html(page.text, page.final_url or list_url):
                if stub.external_job_id in seen:
                    continue
                seen.add(stub.external_job_id)
                stubs.append(stub)

        if not stubs:
            return ListResult(
                stubs=[],
                complete=False,
                fallback_stage="xhr",
                needs_playwright=True,
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
        raw = dict(stub.raw or {})
        apply_url = stub.url or raw.get("apply_url") or raw.get("url")
        listing_url = raw.get("listing_url") or stub.absolute_url
        text = (
            raw.get("twoLineJobDescriptionSummary")
            or raw.get("jobDescriptionSummary")
            or stub.title
        )
        return JobDraft(
            external_job_id=stub.external_job_id,
            title=stub.title,
            url=listing_url or apply_url,
            apply_url=apply_url or listing_url,
            location_raw=stub.location_raw or "Remote",
            workplace_type="remote",
            description_text=str(text) if text else stub.title,
            content_hash=content_hash(str(text) or stub.title),
            posted_at=stub.updated_at,
            salary_min=_salary_num(raw.get("salaryRange"), "min"),
            salary_max=_salary_num(raw.get("salaryRange"), "max"),
            salary_currency=_salary_currency(raw.get("salaryRange")),
            employer_name=raw.get("employer"),
            source_portal="rocketship",
            raw={**raw, "portal": "rocketship"},
        )


def _resolve_slugs(ats_slug: str | None) -> list[str]:
    if not ats_slug:
        return ["software-engineer", "backend-software-engineer", "full-stack-software-engineer"]
    raw = ats_slug.strip()
    if raw.startswith("http"):
        # https://www.remoterocketship.com/jobs/software-engineer/ → software-engineer
        m = re.search(r"/jobs/([^/?#]+)/?", raw)
        return [m.group(1)] if m else ["software-engineer"]
    if "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return [_SLUG_MAP.get(p, p) for p in parts]
    return [_SLUG_MAP.get(raw, raw)]


def _stubs_from_html(html: str, page_url: str) -> list[JobStub]:
    m = _NEXT_DATA.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    page = (data.get("props") or {}).get("pageProps") or {}
    openings = page.get("initialJobOpenings") or []
    stubs: list[JobStub] = []
    for job in openings:
        if not isinstance(job, dict):
            continue
        stub = _stub_from_job(job, page_url)
        if stub:
            stubs.append(stub)
    return stubs


def _stub_from_job(job: dict[str, Any], page_url: str) -> JobStub | None:
    title = (job.get("roleTitle") or job.get("categorizedJobTitle") or "").strip()
    if not title:
        return None
    job_id = job.get("id")
    slug = job.get("slug") or ""
    company = job.get("company") if isinstance(job.get("company"), dict) else {}
    company_slug = (company or {}).get("slug") or "company"
    employer = (company or {}).get("name")
    listing = None
    if slug:
        listing = f"https://www.remoterocketship.com/us/company/{company_slug}/jobs/{slug}/"
    apply_url = job.get("url") or listing
    if not apply_url:
        return None

    external = str(job_id) if job_id is not None else hashlib.sha256(
        f"{title}|{apply_url}".encode()
    ).hexdigest()[:24]

    loc_parts = []
    if job.get("locationCity"):
        loc_parts.append(str(job["locationCity"]))
    if job.get("location"):
        loc_parts.append(str(job["location"]))
    location = ", ".join(loc_parts) if loc_parts else "Remote"
    if str(job.get("locationType") or "").lower() == "remote" and "remote" not in location.lower():
        location = f"{location} (Remote)" if loc_parts else "Remote"

    return JobStub(
        external_job_id=f"rr-{external}",
        title=title,
        url=str(apply_url),
        absolute_url=listing or str(apply_url),
        location_raw=location,
        updated_at=parse_datetime(job.get("created_at")),
        raw={
            "portal": "rocketship",
            "employer": employer,
            "apply_url": apply_url,
            "listing_url": listing,
            "salaryRange": job.get("salaryRange"),
            "jobDescriptionSummary": job.get("jobDescriptionSummary"),
            "twoLineJobDescriptionSummary": job.get("twoLineJobDescriptionSummary"),
            "techStack": job.get("techStack"),
            "id": job_id,
            "page": page_url,
        },
    )


def _salary_num(salary: Any, key: str) -> float | None:
    if not isinstance(salary, dict):
        return None
    val = salary.get(key)
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _salary_currency(salary: Any) -> str | None:
    if not isinstance(salary, dict):
        return None
    cur = salary.get("currencyCode")
    return str(cur) if cur else None
