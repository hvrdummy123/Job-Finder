from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.connectors.util import content_hash, fingerprint_ids, html_to_text, join_locations, parse_datetime
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

_SR_RE = re.compile(
    r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)",
    re.I,
)
_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"


class SmartRecruitersConnector:
    name = "smartrecruiters"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        slug = _slug_from_url(url)
        if slug:
            return DetectResult(ats_type=self.name, ats_slug=slug, confidence=0.95, fallback_stage="api")
        if response:
            m = _SR_RE.search(response.text or "")
            if m:
                return DetectResult(
                    ats_type=self.name,
                    ats_slug=m.group(1),
                    confidence=0.9,
                    fallback_stage="api",
                )
            if "smartrecruiters.com" in (response.text or "").lower():
                return DetectResult(ats_type=self.name, confidence=0.55, fallback_stage="api")
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        slug = source.ats_slug or _slug_from_url(company.careers_url)
        if not slug:
            return ListResult(stubs=[], complete=False)

        stubs: list[JobStub] = []
        offset = 0
        limit = 100
        total = None
        while offset < 2000:
            url = _API.format(slug=slug)
            resp = await client.request(
                "GET", url, params={"limit": limit, "offset": offset}
            )
            if resp.status_code >= 400:
                return ListResult(stubs=stubs, complete=False)
            data = resp.json()
            if total is None:
                total = data.get("totalFound")
            content = data.get("content") or []
            if not isinstance(content, list) or not content:
                break
            for job in content:
                job_id = str(job.get("id") or job.get("uuid") or "")
                if not job_id:
                    continue
                apply = (
                    job.get("postingUrl")
                    or job.get("applyUrl")
                    or f"https://jobs.smartrecruiters.com/{slug}/{job_id}"
                )
                stubs.append(
                    JobStub(
                        external_job_id=job_id,
                        title=job.get("name") or "Untitled",
                        url=apply,
                        location_raw=_location(job),
                        absolute_url=apply,
                        updated_at=parse_datetime(job.get("releasedDate")),
                        raw=job,
                    )
                )
            offset += len(content)
            if total is not None and offset >= int(total):
                break
            if len(content) < limit:
                break

        complete = True
        if total is not None and len(stubs) < int(total):
            complete = False
        return ListResult(
            stubs=stubs,
            complete=complete,
            fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
            fallback_stage="api",
        )

    async def get_job(
        self,
        company: Company,
        source: Source,
        stub: JobStub,
        client: HttpClient,
    ) -> JobDraft:
        slug = source.ats_slug or _slug_from_url(company.careers_url)
        job = stub.raw or {}
        uuid = job.get("uuid") or stub.external_job_id
        html = None
        text = None
        apply_url = stub.url
        if slug and uuid:
            detail_url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{uuid}"
            try:
                resp = await client.request("GET", detail_url)
                if resp.status_code < 400:
                    job = resp.json()
                    apply_url = job.get("postingUrl") or job.get("applyUrl") or apply_url
                    ad = job.get("jobAd") or {}
                    sections = ad.get("sections") if isinstance(ad, dict) else None
                    if isinstance(sections, dict):
                        parts = []
                        for key in ("jobDescription", "qualifications", "additionalInformation"):
                            sec = sections.get(key) or {}
                            if isinstance(sec, dict) and sec.get("text"):
                                parts.append(str(sec["text"]))
                        html = "\n".join(parts) if parts else None
                        text = html_to_text(html) if html else None
            except Exception:
                pass

        return JobDraft(
            external_job_id=str(job.get("id") or stub.external_job_id),
            title=job.get("name") or stub.title,
            url=apply_url,
            apply_url=apply_url,
            location_raw=_location(job) or stub.location_raw,
            department=_name(job.get("department")),
            employment_type=_name(job.get("typeOfEmployment")),
            description_html=html,
            description_text=text,
            content_hash=content_hash(text or stub.title),
            posted_at=parse_datetime(job.get("releasedDate")),
            raw=job,
        )


def _slug_from_url(url: str) -> str | None:
    m = _SR_RE.search(url or "")
    if m:
        return m.group(1)
    return None


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("label") or value.get("name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _location(job: dict[str, Any]) -> str | None:
    loc = job.get("location")
    if isinstance(loc, dict):
        return loc.get("fullLocation") or join_locations(
            loc.get("city"), loc.get("region"), loc.get("country")
        )
    if isinstance(loc, str):
        return loc
    return None
