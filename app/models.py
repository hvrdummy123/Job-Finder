from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Company:
    id: int
    name: str
    careers_url: str
    website: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class Source:
    id: int
    company_id: int
    ats_type: str
    ats_slug: str | None = None
    ats_override: bool = False
    poll_interval_seconds: int = 1800
    next_poll_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    last_etag: str | None = None
    last_modified: str | None = None
    last_fingerprint: str | None = None
    failure_count: int = 0
    last_error: str | None = None
    circuit_open_until: datetime | None = None
    baseline_completed: bool = False
    baseline_completed_at: datetime | None = None
    robots_allowed: bool | None = None
    detect_confidence: float | None = None
    fallback_stage: str = "unknown"
    needs_playwright: bool = False


@dataclass(slots=True)
class JobStub:
    external_job_id: str
    title: str
    url: str | None = None
    location_raw: str | None = None
    absolute_url: str | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobDraft:
    external_job_id: str
    title: str
    url: str | None = None
    apply_url: str | None = None
    location_raw: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    workplace_type: str = "unknown"
    employment_type: str | None = None
    department: str | None = None
    seniority: str | None = None
    yoe_min: int | None = None
    yoe_max: int | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    description_text: str | None = None
    description_html: str | None = None
    skills: list[str] = field(default_factory=list)
    content_hash: str | None = None
    posted_at: datetime | None = None
    source_portal: str | None = None
    employer_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ListResult:
    stubs: list[JobStub]
    complete: bool
    etag: str | None = None
    fingerprint: str | None = None
    last_modified: str | None = None
    fallback_stage: str | None = None
    needs_playwright: bool | None = None


@dataclass(slots=True)
class DetectResult:
    ats_type: str
    ats_slug: str | None = None
    confidence: float = 1.0
    fallback_stage: str = "api"
    needs_playwright: bool = False


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    text: str
    content: bytes
