from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from app.connectors.util import content_hash, fingerprint_ids, html_to_text, join_locations, parse_datetime
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

# Public JSON search used by amazon.jobs frontend
_SEARCH = "https://www.amazon.jobs/en/search.json"


class AmazonConnector:
    name = "amazon"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        host = urlparse(url).netloc.lower()
        if "amazon.jobs" in host or "amazon.jobs" in url:
            return DetectResult(
                ats_type=self.name,
                ats_slug="software-development",
                confidence=0.95,
                fallback_stage="api",
            )
        if response and "amazon.jobs" in (response.final_url or "").lower():
            return DetectResult(
                ats_type=self.name,
                ats_slug="software-development",
                confidence=0.9,
                fallback_stage="api",
            )
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        category = source.ats_slug or "software-development"
        stubs: list[JobStub] = []
        offset = 0
        page_size = 100
        total: int | None = None

        while offset < 500:  # hard cap pages
            params = {
                "offset": offset,
                "result_limit": page_size,
                "sort": "relevant",
                "category[]": category,
            }
            # Also pull broadly for engineer query if category-specific fails empty
            resp = await client.request("GET", _SEARCH, params=params)
            if resp.status_code >= 400:
                if offset == 0:
                    # fallback query
                    resp = await client.request(
                        "GET",
                        _SEARCH,
                        params={
                            "offset": 0,
                            "result_limit": page_size,
                            "base_query": "software engineer",
                            "sort": "relevant",
                        },
                    )
                    if resp.status_code >= 400:
                        return ListResult(stubs=[], complete=False)
                else:
                    return ListResult(stubs=stubs, complete=False)

            data = resp.json()
            jobs = data.get("jobs") or []
            if total is None:
                total = data.get("hits")
            if not isinstance(jobs, list) or not jobs:
                break

            for job in jobs:
                job_id = str(job.get("id_icims") or job.get("id") or job.get("job_path") or "")
                if not job_id:
                    continue
                path = job.get("job_path") or ""
                url = f"https://www.amazon.jobs{path}" if path.startswith("/") else path
                stubs.append(
                    JobStub(
                        external_job_id=job_id,
                        title=job.get("title") or "Untitled",
                        url=url,
                        location_raw=_location(job),
                        updated_at=parse_datetime(job.get("posted_date")),
                        raw=job,
                    )
                )

            offset += len(jobs)
            if total is not None and offset >= int(total):
                break
            if len(jobs) < page_size:
                break

        complete = True
        if total is not None and len(stubs) < min(int(total), 500):
            # capped intentionally
            complete = len(stubs) >= min(int(total), 500) or offset >= 500

        return ListResult(
            stubs=stubs,
            complete=complete if stubs else False,
            fingerprint=fingerprint_ids([s.external_job_id for s in stubs]),
            fallback_stage="api",
            needs_playwright=False,
        )

    async def get_job(
        self,
        company: Company,
        source: Source,
        stub: JobStub,
        client: HttpClient,
    ) -> JobDraft:
        job = stub.raw or {}
        # Prefer fields already on search result; optionally fetch HTML detail
        html = job.get("description_short") or ""
        text = html_to_text(str(html)) if html else ""
        if stub.url and (not text or len(text) < 80):
            try:
                page = await client.get_text(stub.url, respect_robots=True)
                if page.status_code < 400:
                    text = html_to_text(page.text) or text
                    html = page.text
            except Exception:
                pass

        return JobDraft(
            external_job_id=stub.external_job_id,
            title=stub.title,
            url=stub.url,
            apply_url=stub.url,
            location_raw=_location(job) or stub.location_raw,
            description_html=html if isinstance(html, str) else None,
            description_text=text or None,
            content_hash=content_hash(text or stub.title),
            posted_at=stub.updated_at or parse_datetime(job.get("posted_date")),
            department=job.get("job_category") or job.get("team"),
            raw={**job, "portal": None},
        )


def _location(job: dict[str, Any]) -> str | None:
    """Amazon search results include a locations[] of JSON strings for multi-city roles."""
    parsed: list[Any] = []
    for item in job.get("locations") or []:
        if isinstance(item, str):
            try:
                obj = json.loads(item)
            except json.JSONDecodeError:
                parsed.append(item)
                continue
            if isinstance(obj, dict):
                # Prefer ISO triple ("US, WA, Seattle") — one entry per city
                parsed.append(obj.get("location") or obj.get("normalizedLocation"))
            else:
                parsed.append(item)
        elif isinstance(item, dict):
            parsed.append(item.get("location") or item.get("normalizedLocation") or item)
        else:
            parsed.append(item)
    if parsed:
        return join_locations(*parsed)
    return join_locations(job.get("location"), job.get("normalized_location"), job.get("city"))
