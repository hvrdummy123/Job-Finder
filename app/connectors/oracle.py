from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from app.connectors.util import content_hash, fingerprint_ids, html_to_text, join_locations, parse_datetime
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

# slug: "{oracle_host}|{site_number}|{portal_base}"
# e.g. egug.fa.us2.oraclecloud.com|CX_1|https://careers.americanexpress.com
_ORACLE_HOST_RE = re.compile(
    r"(?P<host>[a-z0-9.-]+\.oraclecloud\.com)",
    re.I,
)
_SITE_RE = re.compile(r"sites?/(?P<site>CX_\d+)", re.I)
_API = (
    "https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    "?onlyData=true"
    "&expand=requisitionList.secondaryLocations"
    "&finder=findReqs;siteNumber={site},facetsList=LOCATIONS;WORK_LOCATIONS;TITLES;CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS"
    ",limit={limit},offset={offset}"
)


class OracleConnector:
    name = "oracle"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        parsed = _parse_slug_parts(url, response.text if response else None)
        if parsed:
            host, site, portal = parsed
            return DetectResult(
                ats_type=self.name,
                ats_slug=_pack_slug(host, site, portal or url),
                confidence=0.9,
                fallback_stage="api",
            )
        text = (response.text if response else "") or ""
        if "oraclecloud.com" in text.lower() and ("recruitingCE" in text or "CX_" in text):
            return DetectResult(ats_type=self.name, confidence=0.55, fallback_stage="api")
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        parts = _unpack_slug(source.ats_slug) or _parse_slug_parts(company.careers_url, None)
        if not parts:
            return ListResult(stubs=[], complete=False)
        host, site, portal = parts
        portal = portal or company.careers_url

        stubs: list[JobStub] = []
        offset = 0
        limit = 50
        total = None
        while offset < 2000:
            url = _API.format(host=host, site=site, limit=limit, offset=offset)
            resp = await client.request("GET", url)
            if resp.status_code >= 400:
                return ListResult(stubs=stubs, complete=False)
            data = resp.json()
            items = data.get("items") or []
            if not items:
                break
            block = items[0] if isinstance(items[0], dict) else {}
            if total is None:
                total = block.get("TotalJobsCount")
            reqs = block.get("requisitionList") or []
            if not isinstance(reqs, list) or not reqs:
                break
            for job in reqs:
                job_id = str(job.get("Id") or "")
                if not job_id:
                    continue
                job_url = _job_url(portal, site, job_id, job.get("Title"))
                stubs.append(
                    JobStub(
                        external_job_id=job_id,
                        title=job.get("Title") or "Untitled",
                        url=job_url,
                        location_raw=_location(job),
                        absolute_url=job_url,
                        updated_at=parse_datetime(job.get("PostedDate")),
                        raw={**job, "_oracle_host": host, "_oracle_site": site, "_portal": portal},
                    )
                )
            offset += len(reqs)
            if total is not None and offset >= int(total):
                break
            if len(reqs) < limit:
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
        job = stub.raw or {}
        text = html_to_text(
            " ".join(
                str(job.get(k) or "")
                for k in (
                    "ShortDescriptionStr",
                    "ExternalQualificationsStr",
                    "ExternalResponsibilitiesStr",
                )
            )
        )
        return JobDraft(
            external_job_id=stub.external_job_id,
            title=stub.title,
            url=stub.url,
            apply_url=stub.url,
            location_raw=_location(job) or stub.location_raw,
            workplace_type=_workplace(job.get("WorkplaceType") or job.get("WorkplaceTypeCode")),
            description_text=text or None,
            content_hash=content_hash(text or stub.title),
            posted_at=parse_datetime(job.get("PostedDate")),
            raw=job,
        )


def _pack_slug(host: str, site: str, portal: str) -> str:
    return f"{host}|{site}|{portal.rstrip('/')}"


def _unpack_slug(slug: str | None) -> tuple[str, str, str] | None:
    if not slug or "|" not in slug:
        return None
    parts = slug.split("|", 2)
    if len(parts) < 2:
        return None
    host, site = parts[0], parts[1]
    portal = parts[2] if len(parts) > 2 else ""
    if not host or not site:
        return None
    return host, site, portal


def _parse_slug_parts(url: str | None, text: str | None) -> tuple[str, str, str] | None:
    blob = " ".join(x for x in (url or "", text or "") if x)
    host_m = _ORACLE_HOST_RE.search(blob)
    site_m = _SITE_RE.search(blob)
    if not host_m:
        return None
    host = host_m.group("host")
    site = site_m.group("site") if site_m else "CX_1"
    portal = ""
    if url and "oraclecloud.com" not in url:
        portal = url.split("?")[0]
    elif url:
        # Prefer a non-API careers host if present in text
        for m in re.finditer(r"https?://(?![\w.-]*oraclecloud)[\w.-]+/[^\"'\s]*sites/CX_\d+", blob, re.I):
            portal = m.group(0).split("/sites/")[0]
            break
    return host, site, portal or (url or "")


def _job_url(portal: str, site: str, job_id: str, title: str | None) -> str:
    base = portal.rstrip("/")
    if "/sites/" not in base:
        # americanexpress style: https://careers.americanexpress.com/en/sites/CX_1
        if base.endswith(".com"):
            base = f"{base}/en/sites/{site}"
        else:
            base = f"{base}/sites/{site}"
    # Pretty path optional; Id is enough for Oracle CE
    return f"{base}/job/{job_id}"


def _location(job: dict[str, Any]) -> str | None:
    primary = job.get("PrimaryLocation")
    extras: list[Any] = []
    for item in job.get("secondaryLocations") or []:
        if isinstance(item, dict):
            extras.append(item.get("PrimaryLocation") or item.get("Location") or item.get("Name"))
        else:
            extras.append(item)
    return join_locations(primary, *extras)


def _workplace(value: Any) -> str:
    text = str(value or "").lower()
    if "remote" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if text:
        return "onsite"
    return "unknown"
