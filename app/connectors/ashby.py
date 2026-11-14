from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.connectors.util import content_hash, fingerprint_ids, html_to_text, join_locations, parse_datetime
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

_ASHBY_RE = re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)", re.I)


class AshbyConnector:
    name = "ashby"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        slug = _slug_from_url(url)
        if slug:
            return DetectResult(ats_type=self.name, ats_slug=slug, confidence=0.95, fallback_stage="api")
        if response:
            m = _ASHBY_RE.search(response.text)
            if m:
                return DetectResult(
                    ats_type=self.name,
                    ats_slug=m.group(1),
                    confidence=0.9,
                    fallback_stage="api",
                )
            if "ashbyhq.com" in response.text.lower():
                return DetectResult(ats_type=self.name, confidence=0.55, fallback_stage="api")
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        slug = source.ats_slug or _slug_from_url(company.careers_url)
        if not slug:
            return ListResult(stubs=[], complete=False)

        # Public job board API used by Ashby-hosted boards
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        resp = await client.request("GET", url, params={"includeCompensation": "true"})
        if resp.status_code >= 400:
            # Fall back to alternate posting API URL shape
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
            resp = await client.request("GET", url)
            if resp.status_code >= 400:
                return ListResult(stubs=[], complete=False)

        data = resp.json()
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if jobs is None and isinstance(data, dict):
            jobs = data.get("jobPostings") or data.get("results")
        if not isinstance(jobs, list):
            return ListResult(stubs=[], complete=False)

        stubs: list[JobStub] = []
        for job in jobs:
            job_id = str(job.get("id") or job.get("jobId") or job.get("jobPostingId") or "")
            if not job_id:
                continue
            stubs.append(
                JobStub(
                    external_job_id=job_id,
                    title=job.get("title") or "Untitled",
                    url=job.get("jobUrl") or job.get("applyUrl") or f"https://jobs.ashbyhq.com/{slug}/{job_id}",
                    location_raw=_location(job),
                    absolute_url=job.get("jobUrl"),
                    updated_at=parse_datetime(job.get("publishedAt") or job.get("updatedAt")),
                    raw=job,
                )
            )
        return ListResult(
            stubs=stubs,
            complete=True,
            etag=resp.headers.get("etag"),
            fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
        )

    async def get_job(
        self,
        company: Company,
        source: Source,
        stub: JobStub,
        client: HttpClient,
    ) -> JobDraft:
        job = stub.raw or {}
        slug = source.ats_slug or _slug_from_url(company.careers_url)

        # Prefer description already on the stub; otherwise fetch posting detail if available
        html = job.get("descriptionHtml") or job.get("description") or ""
        text = job.get("descriptionPlain") or html_to_text(html)
        if not text and slug:
            detail_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}/job/{stub.external_job_id}"
            resp = await client.request("GET", detail_url)
            if resp.status_code < 400:
                job = resp.json()
                html = job.get("descriptionHtml") or job.get("description") or html
                text = job.get("descriptionPlain") or html_to_text(html)

        apply_url = job.get("applyUrl") or job.get("jobUrl") or stub.url
        return JobDraft(
            external_job_id=str(job.get("id") or stub.external_job_id),
            title=job.get("title") or stub.title,
            url=job.get("jobUrl") or stub.url,
            apply_url=apply_url,
            location_raw=_location(job) or stub.location_raw,
            department=_department(job),
            employment_type=job.get("employmentType"),
            description_html=html if isinstance(html, str) else None,
            description_text=text,
            content_hash=content_hash(text or stub.title),
            posted_at=parse_datetime(job.get("publishedAt") or job.get("updatedAt")),
            workplace_type=_workplace(job),
            raw=job,
        )


def _slug_from_url(url: str) -> str | None:
    m = _ASHBY_RE.search(url)
    if m:
        return m.group(1)
    host = urlparse(url).netloc.lower()
    if "ashbyhq.com" in host:
        parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        return parts[0] if parts else None
    return None


def _location(job: dict[str, Any]) -> str | None:
    primary: Any = job.get("location")
    if isinstance(primary, dict):
        primary = primary.get("name") or primary.get("location")

    secondary_parts: list[Any] = []
    for item in job.get("secondaryLocations") or []:
        if isinstance(item, dict):
            secondary_parts.append(item.get("location") or item.get("name"))
        else:
            secondary_parts.append(item)

    locs = job.get("locations")
    merged = join_locations(primary, secondary_parts, locs)
    if merged:
        return merged

    addr = job.get("address")
    if isinstance(addr, dict):
        postal = addr.get("postalAddress") if isinstance(addr.get("postalAddress"), dict) else addr
        if isinstance(postal, dict):
            return join_locations(
                postal.get("addressLocality"),
                postal.get("addressRegion"),
                postal.get("addressCountry"),
            )
    return None


def _department(job: dict[str, Any]) -> str | None:
    dept = job.get("department")
    if isinstance(dept, str):
        return dept
    if isinstance(dept, dict):
        return dept.get("name")
    team = job.get("team")
    if isinstance(team, str):
        return team
    return None


def _workplace(job: dict[str, Any]) -> str:
    wt = str(job.get("workplaceType") or job.get("isRemote") or "").lower()
    if wt in {"true", "1"} or "remote" in wt:
        return "remote"
    if "hybrid" in wt:
        return "hybrid"
    if "office" in wt or "on-site" in wt or "onsite" in wt:
        return "onsite"
    return "unknown"
