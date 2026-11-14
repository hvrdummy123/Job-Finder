from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.connectors.util import (
    content_hash,
    fingerprint_ids,
    html_to_text,
    join_locations,
    normalize_title,
    parse_datetime,
)
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

_BOARD_RE = re.compile(
    r"(?:boards(?:-api)?\.greenhouse\.io)/(?:embed/job_board\?for=)?([a-zA-Z0-9_-]+)",
    re.I,
)
_BOARD_FOR_RE = re.compile(r"[?&]for=([a-zA-Z0-9_-]+)", re.I)
_BAD_SLUGS = {"embed", "v1", "boards", "api", "job-board", "job_board", "for", "jobs", "job"}
_GH_JOB_URL = "https://boards.greenhouse.io/{slug}/jobs/{job_id}"


class GreenhouseConnector:
    name = "greenhouse"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        slug = _slug_from_url(url)
        if slug:
            return DetectResult(ats_type=self.name, ats_slug=slug, confidence=0.95, fallback_stage="api")
        if response:
            text = response.text
            m = _BOARD_FOR_RE.search(text) or _BOARD_RE.search(text) or re.search(
                r"boards\.greenhouse\.io/([a-zA-Z0-9_-]+)", text, re.I
            )
            if m:
                slug = next((g for g in m.groups() if g), None)
                if slug and slug.lower() not in _BAD_SLUGS:
                    return DetectResult(
                        ats_type=self.name,
                        ats_slug=slug,
                        confidence=0.9,
                        fallback_stage="api",
                    )
            if "greenhouse.io" in text.lower() or "greenhouse-job-board" in text.lower():
                return DetectResult(ats_type=self.name, confidence=0.6, fallback_stage="api")
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        slug = source.ats_slug or _slug_from_url(company.careers_url)
        if not slug:
            return ListResult(stubs=[], complete=False)

        # content=true returns JD HTML in the list — avoids N+1 detail fetches
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        headers: dict[str, str] = {}
        # Never send conditional headers before baseline — a 304 with empty stubs
        # would mark baseline complete with zero jobs.
        if source.baseline_completed:
            if source.last_etag:
                headers["If-None-Match"] = source.last_etag
            if source.last_modified:
                headers["If-Modified-Since"] = source.last_modified

        resp = await client.request(
            "GET", url, headers=headers or None, params={"content": "true"}
        )
        if resp.status_code == 304:
            return ListResult(
                stubs=[],
                complete=True,
                etag=source.last_etag,
                fingerprint=source.last_fingerprint,
                last_modified=source.last_modified,
            )
        if resp.status_code >= 400:
            return ListResult(stubs=[], complete=False)

        data = resp.json()
        jobs = data.get("jobs") if isinstance(data, dict) else data
        if not isinstance(jobs, list):
            return ListResult(stubs=[], complete=False)

        stubs: list[JobStub] = []
        for job in jobs:
            job_id = str(job.get("id"))
            stubs.append(
                JobStub(
                    external_job_id=job_id,
                    title=job.get("title") or "Untitled",
                    url=job.get("absolute_url") or _GH_JOB_URL.format(slug=slug, job_id=job_id),
                    location_raw=_location(job),
                    absolute_url=job.get("absolute_url"),
                    updated_at=parse_datetime(job.get("updated_at")),
                    raw=job,
                )
            )
        ids = [s.external_job_id for s in stubs]
        return ListResult(
            stubs=stubs,
            complete=True,
            etag=resp.headers.get("etag"),
            fingerprint=fingerprint_ids(ids),
            last_modified=resp.headers.get("last-modified"),
        )

    async def get_job(
        self,
        company: Company,
        source: Source,
        stub: JobStub,
        client: HttpClient,
    ) -> JobDraft:
        slug = source.ats_slug or _slug_from_url(company.careers_url)
        if not slug:
            raise ValueError("Greenhouse slug missing")

        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{stub.external_job_id}"
        resp = await client.request("GET", url)
        resp.raise_for_status()
        job: dict[str, Any] = resp.json()
        html = job.get("content") or ""
        text = html_to_text(html)
        apply_url = job.get("absolute_url") or stub.url
        return JobDraft(
            external_job_id=str(job.get("id") or stub.external_job_id),
            title=job.get("title") or stub.title,
            url=apply_url,
            apply_url=apply_url,
            location_raw=_location(job) or stub.location_raw,
            department=_department(job),
            employment_type=_meta(job, "employment_type"),
            description_html=html,
            description_text=text,
            content_hash=content_hash(text or stub.title),
            posted_at=parse_datetime(job.get("updated_at") or job.get("created_at")),
            raw=job,
        )


def _slug_from_url(url: str) -> str | None:
    if not url:
        return None
    # embed/job_board?for=brave
    m = _BOARD_FOR_RE.search(url)
    if m and m.group(1).lower() not in _BAD_SLUGS:
        return m.group(1)
    m = _BOARD_RE.search(url)
    if m and m.group(1).lower() not in _BAD_SLUGS:
        return m.group(1)
    host = urlparse(url).netloc.lower()
    if "greenhouse.io" in host:
        parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        if parts and parts[0].lower() not in _BAD_SLUGS:
            return parts[0]
    return None


def _location(job: dict[str, Any]) -> str | None:
    # Prefer offices[] — Greenhouse often lists every city there while
    # location.name is a single joined marketing string.
    office_parts: list[Any] = []
    for office in job.get("offices") or []:
        if not isinstance(office, dict):
            continue
        office_parts.append(office.get("location") or office.get("name"))
    from_offices = join_locations(*office_parts)
    if from_offices and ";" in from_offices:
        return from_offices

    loc = job.get("location")
    if isinstance(loc, dict):
        name = loc.get("name")
        return from_offices or (name.strip() if isinstance(name, str) and name.strip() else None)
    if isinstance(loc, str) and loc.strip():
        return from_offices or loc.strip()
    return from_offices


def _department(job: dict[str, Any]) -> str | None:
    deps = job.get("departments") or []
    if deps and isinstance(deps[0], dict):
        return deps[0].get("name")
    return None


def _meta(job: dict[str, Any], key: str) -> str | None:
    for item in job.get("metadata") or []:
        if isinstance(item, dict) and str(item.get("name", "")).lower() == key:
            return str(item.get("value")) if item.get("value") is not None else None
    return None
