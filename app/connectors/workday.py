from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from app.connectors.util import content_hash, fingerprint_ids, html_to_text, parse_datetime
from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source

log = logging.getLogger(__name__)

# slug format: {tenant}/{site}  e.g. adobe/external_experienced
_WD_HOST_RE = re.compile(
    r"(?P<tenant>[a-z0-9_-]+)\.(?:wd\d+\.)?myworkdayjobs\.com",
    re.I,
)
_WD_PATH_RE = re.compile(
    r"myworkdayjobs\.com/(?:[a-z]{2}-[a-z]{2}/)?(?P<site>[a-zA-Z0-9_-]+)",
    re.I,
)
# Phenom / branded careers pages often embed absolute Workday apply links
_WD_EMBED_RE = re.compile(
    r"https?://(?P<tenant>[a-z0-9_-]+)\.(?P<host>wd\d+\.)?myworkdayjobs\.com/"
    r"(?:[a-z]{2}-[a-z]{2}/)?(?P<site>[a-zA-Z0-9_-]+)",
    re.I,
)


class WorkdayConnector:
    name = "workday"

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None:
        slug = _slug_from_url(url)
        if slug:
            return DetectResult(ats_type=self.name, ats_slug=slug, confidence=0.95, fallback_stage="api")
        if response:
            final = response.final_url
            slug = _slug_from_url(final)
            if slug:
                return DetectResult(ats_type=self.name, ats_slug=slug, confidence=0.9, fallback_stage="api")
            embedded = _slug_from_html(response.text or "")
            if embedded:
                return DetectResult(
                    ats_type=self.name,
                    ats_slug=embedded,
                    confidence=0.88,
                    fallback_stage="api",
                )
            if "myworkdayjobs.com" in response.text.lower() or "workday" in response.text.lower():
                return DetectResult(ats_type=self.name, confidence=0.5, fallback_stage="api")
        return None

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult:
        slug = source.ats_slug or _slug_from_url(company.careers_url)
        if not slug or "/" not in slug:
            return ListResult(stubs=[], complete=False, fallback_stage="api")

        tenant, site = slug.split("/", 1)
        site = site.split("?", 1)[0].strip("/")
        base = await _resolve_cxs_base(client, tenant, site, company.careers_url)
        if not base:
            return ListResult(stubs=[], complete=False, fallback_stage="api")

        url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
        stubs: list[JobStub] = []
        offset = 0
        page_size = 20
        total: int | None = None

        while True:
            body = {"appliedFacets": {}, "limit": page_size, "offset": offset, "searchText": ""}
            resp = await client.request("POST", url, json=body)
            if resp.status_code >= 400:
                return ListResult(stubs=stubs, complete=False, fallback_stage="api")

            data = resp.json()
            postings = data.get("jobPostings") or []
            if total is None:
                total = data.get("total")
            if not isinstance(postings, list):
                return ListResult(stubs=stubs, complete=False, fallback_stage="api")

            for job in postings:
                external_path = job.get("externalPath") or ""
                if not external_path:
                    continue
                # Keep Workday path as id (e.g. /job/Bangalore/Title_R123)
                job_id = external_path if external_path.startswith("/") else f"/{external_path}"
                job_link = _job_url(base, site, job_id)
                loc_text = (job.get("locationsText") or "").strip()
                # "2 Locations" is useless — detail fetch fills real cities
                if re.fullmatch(r"\d+\s+Locations?", loc_text, re.I):
                    loc_text = None
                stubs.append(
                    JobStub(
                        external_job_id=job_id,
                        title=job.get("title") or "Untitled",
                        url=job_link,
                        location_raw=loc_text,
                        absolute_url=job_link,
                        updated_at=parse_datetime(job.get("postedOn")),
                        raw={**job, "_cxs_base": base},
                    )
                )

            offset += len(postings)
            if not postings:
                break
            if total is not None and offset >= int(total):
                break
            if len(postings) < page_size:
                break

        complete = True
        if total is not None and len(stubs) < int(total):
            complete = False

        return ListResult(
            stubs=stubs,
            complete=complete,
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
        slug = source.ats_slug or _slug_from_url(company.careers_url)
        if not slug or "/" not in slug:
            raise ValueError("Workday slug missing")
        tenant, site = slug.split("/", 1)
        site = site.split("?", 1)[0].strip("/")
        base = (stub.raw or {}).get("_cxs_base") if isinstance(stub.raw, dict) else None
        base = base or await _resolve_cxs_base(client, tenant, site, company.careers_url)
        if not base:
            return JobDraft(
                external_job_id=stub.external_job_id,
                title=stub.title,
                url=stub.url,
                apply_url=stub.url,
                location_raw=stub.location_raw,
                description_text=None,
                content_hash=content_hash(stub.title),
                raw=stub.raw,
            )
        # externalPath is already "/job/..." — do not prefix another "/job"
        path = stub.external_job_id
        if not path.startswith("/"):
            path = "/" + path
        detail_url = f"{base}/wday/cxs/{tenant}/{site}{path}"
        resp = await client.request("GET", detail_url)
        if resp.status_code >= 400:
            return JobDraft(
                external_job_id=stub.external_job_id,
                title=stub.title,
                url=stub.url,
                apply_url=stub.url,
                location_raw=stub.location_raw,
                description_text=None,
                content_hash=content_hash(stub.title),
                raw=stub.raw,
            )

        data = resp.json()
        info = data.get("jobPostingInfo") or data
        html = info.get("jobDescription") or ""
        text = html_to_text(html)
        apply_url = info.get("externalUrl") or stub.url
        location_raw = _merge_workday_locations(info, stub.location_raw)
        return JobDraft(
            external_job_id=stub.external_job_id,
            title=info.get("title") or stub.title,
            url=stub.url,
            apply_url=apply_url,
            location_raw=location_raw,
            department=_join(info.get("jobBoard")),
            employment_type=info.get("timeType"),
            description_html=html,
            description_text=text,
            content_hash=content_hash(text or stub.title),
            posted_at=parse_datetime(info.get("startDate") or info.get("postedOn")),
            raw=data,
        )


def _slug_from_url(url: str) -> str | None:
    host_m = _WD_HOST_RE.search(urlparse(url).netloc)
    path_m = _WD_PATH_RE.search(url)
    tenant = host_m.group("tenant") if host_m else None
    site = path_m.group("site") if path_m else None
    if not site:
        parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        parts = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[a-z]{2}", p, re.I)]
        if parts:
            site = parts[0]
    if tenant and site:
        return f"{tenant}/{site}"
    if url.count("/") == 1 and not url.startswith("http"):
        return url
    return None


def _slug_from_html(html: str) -> str | None:
    counts: dict[str, int] = {}
    for m in _WD_EMBED_RE.finditer(html):
        slug = f"{m.group('tenant')}/{m.group('site')}"
        # Prefer career site boards over /job/ deep links' first path segment
        site = m.group("site")
        if site.lower() in {"job", "jobs", "apply"}:
            continue
        counts[slug] = counts.get(slug, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


async def _resolve_cxs_base(
    client: HttpClient, tenant: str, site: str, careers_url: str
) -> str | None:
    """Find the live Workday host (wd5 etc.). Phenom frontends are not CXS hosts."""
    cache_key = f"{tenant}/{site}"
    if cache_key in _CXS_BASE_CACHE:
        return _CXS_BASE_CACHE[cache_key]

    candidates: list[str] = []
    parsed = urlparse(careers_url)
    netloc = (parsed.netloc or "").lower()
    if "myworkdayjobs.com" in netloc:
        candidates.append(f"{parsed.scheme}://{parsed.netloc}")
    # Prefer discovering host from the careers HTML when branded
    if careers_url.startswith("http") and "myworkdayjobs.com" not in netloc:
        try:
            page = await client.get_text(careers_url, respect_robots=True)
            for m in _WD_EMBED_RE.finditer(page.text or ""):
                if m.group("tenant").lower() != tenant.lower():
                    continue
                # host group is e.g. "wd5." or None
                host = f"{m.group('tenant')}.{(m.group('host') or '')}myworkdayjobs.com"
                candidates.append(f"https://{host}")
        except Exception as exc:
            log.debug("workday host discover failed: %s", exc)

    candidates.append(f"https://{tenant}.myworkdayjobs.com")
    for n in range(1, 6):
        candidates.append(f"https://{tenant}.wd{n}.myworkdayjobs.com")

    seen: set[str] = set()
    probe = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        try:
            resp = await client.request(
                "POST", f"{base}/wday/cxs/{tenant}/{site}/jobs", json=probe
            )
        except Exception:
            continue
        if resp.status_code >= 400:
            continue
        try:
            data = resp.json()
        except Exception:
            continue
        if isinstance(data.get("jobPostings"), list) or data.get("total") is not None:
            _CXS_BASE_CACHE[cache_key] = base
            return base
    return None


def _job_url(base: str, site: str, external_path: str) -> str:
    path = external_path if external_path.startswith("/") else f"/{external_path}"
    return f"{base}/{site}{path}"


def _merge_workday_locations(info: dict[str, Any], fallback: str | None) -> str | None:
    """Combine primary + additionalLocations (e.g. Noida + Bangalore)."""
    country = None
    c = info.get("country")
    if isinstance(c, dict):
        country = c.get("descriptor")
    elif isinstance(c, str):
        country = c
    req = info.get("jobRequisitionLocation")
    if not country and isinstance(req, dict):
        rc = req.get("country")
        if isinstance(rc, dict):
            country = rc.get("descriptor")

    cities: list[str] = []
    primary = _join(info.get("location"))
    if primary:
        cities.append(primary.strip())
    additional = info.get("additionalLocations") or []
    if isinstance(additional, str):
        additional = [additional]
    if isinstance(additional, list):
        for item in additional:
            name = _join(item) if not isinstance(item, str) else item
            if not name:
                continue
            name = name.strip()
            if name and name.lower() not in {c.lower() for c in cities}:
                cities.append(name)

    if not cities:
        fb = (fallback or "").strip()
        if fb and not re.fullmatch(r"\d+\s+Locations?", fb, re.I):
            return fb
        return None

    # Annotate with country when Workday only returns city names
    if country:
        enriched = []
        for city in cities:
            if country.lower() in city.lower():
                enriched.append(city)
            else:
                enriched.append(f"{city}, {country}")
        cities = enriched

    return "; ".join(cities)


# Cache resolved CXS hosts for the process lifetime (tenant/site → base URL)
_CXS_BASE_CACHE: dict[str, str] = {}


def _join(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("name") or value.get("descriptor")
    return None
