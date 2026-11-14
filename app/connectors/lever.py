from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.connectors.util import content_hash, fingerprint_ids, html_to_text, join_locations, parse_datetime
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

_LEVER_RE = re.compile(r"(?:jobs\.)?lever\.co/([a-zA-Z0-9_-]+)", re.I)


class LeverConnector:
    name = "lever"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        slug = _slug_from_url(url)
        if slug:
            return DetectResult(ats_type=self.name, ats_slug=slug, confidence=0.95, fallback_stage="api")
        if response:
            m = _LEVER_RE.search(response.text)
            if m:
                return DetectResult(
                    ats_type=self.name,
                    ats_slug=m.group(1),
                    confidence=0.9,
                    fallback_stage="api",
                )
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        slug = source.ats_slug or _slug_from_url(company.careers_url)
        if not slug:
            return ListResult(stubs=[], complete=False)

        url = f"https://api.lever.co/v0/postings/{slug}"
        headers: dict[str, str] = {}
        if source.baseline_completed and source.last_etag:
            headers["If-None-Match"] = source.last_etag

        resp = await client.request("GET", url, params={"mode": "json"}, headers=headers or None)
        if resp.status_code == 304:
            return ListResult(
                stubs=[],
                complete=True,
                etag=source.last_etag,
                fingerprint=source.last_fingerprint,
            )
        if resp.status_code >= 400:
            return ListResult(stubs=[], complete=False)

        data = resp.json()
        if not isinstance(data, list):
            return ListResult(stubs=[], complete=False)

        stubs: list[JobStub] = []
        for job in data:
            job_id = str(job.get("id") or job.get("additional") or "")
            if not job_id:
                continue
            stubs.append(
                JobStub(
                    external_job_id=job_id,
                    title=job.get("text") or "Untitled",
                    url=job.get("hostedUrl") or job.get("applyUrl"),
                    location_raw=_location(job),
                    absolute_url=job.get("hostedUrl"),
                    updated_at=parse_datetime(job.get("createdAt")),
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
        # Lever list endpoint already returns rich content when mode=json
        job = stub.raw or {}
        if not job.get("description") and not job.get("descriptionPlain"):
            slug = source.ats_slug or _slug_from_url(company.careers_url)
            if slug:
                url = f"https://api.lever.co/v0/postings/{slug}/{stub.external_job_id}"
                resp = await client.request("GET", url)
                if resp.status_code < 400:
                    job = resp.json()

        html = job.get("description") or ""
        text = job.get("descriptionPlain") or html_to_text(html)
        lists = job.get("lists") or []
        if lists and not text:
            parts = []
            for block in lists:
                if isinstance(block, dict):
                    parts.append(str(block.get("text") or ""))
                    parts.append(html_to_text(str(block.get("content") or "")))
            text = "\n".join(p for p in parts if p)

        apply_url = job.get("applyUrl") or job.get("hostedUrl") or stub.url
        cats = job.get("categories") or {}
        return JobDraft(
            external_job_id=str(job.get("id") or stub.external_job_id),
            title=job.get("text") or stub.title,
            url=job.get("hostedUrl") or stub.url,
            apply_url=apply_url,
            location_raw=_location(job) or stub.location_raw,
            department=cats.get("team") or cats.get("department"),
            employment_type=cats.get("commitment"),
            seniority=cats.get("level"),
            description_html=html,
            description_text=text,
            content_hash=content_hash(text or stub.title),
            posted_at=parse_datetime(job.get("createdAt")),
            workplace_type=_workplace(job),
            raw=job,
        )


def _slug_from_url(url: str) -> str | None:
    m = _LEVER_RE.search(url)
    if m:
        return m.group(1)
    host = urlparse(url).netloc.lower()
    if "lever.co" in host:
        parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        return parts[0] if parts else None
    return None


def _location(job: dict[str, Any]) -> str | None:
    cats = job.get("categories") or {}
    all_locs = cats.get("allLocations") or job.get("allLocations")
    merged = join_locations(all_locs, cats.get("location"))
    if merged:
        return merged
    locs = job.get("workplaceType")
    if isinstance(locs, str):
        return locs
    return job.get("country")


def _workplace(job: dict[str, Any]) -> str:
    wt = str(job.get("workplaceType") or "").lower()
    if "remote" in wt:
        return "remote"
    if "hybrid" in wt:
        return "hybrid"
    if wt:
        return "onsite"
    return "unknown"
