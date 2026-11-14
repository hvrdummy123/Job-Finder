from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app import db
from app.config import Settings, get_settings
from app.connectors import registry
from app.connectors.util import content_hash, normalize_title
from app.http_client import HttpClient
from app.ingest.locations import locations_payload, normalize_location, primary_location
from app.ingest.match import match_jobs_to_searches
from app.ingest.parse_jd import parse_jd
from app.ingest.role_filter import is_tech_role, looks_like_job_url
from app.ingest.scrub import scrub_obj, scrub_text
from app.models import Company, JobDraft, JobStub, ListResult, Source
from app.realtime import bus

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RefreshOutcome:
    source_id: int
    company_id: int
    company_name: str
    ok: bool
    was_baseline: bool = False
    jobs_found: int = 0
    jobs_new: int = 0
    jobs_closed: int = 0
    error: str | None = None
    duration_ms: int = 0
    unchanged: bool = False


async def refresh_source(
    source_id: int,
    client: HttpClient,
    *,
    run_id: int | None = None,
    settings: Settings | None = None,
) -> RefreshOutcome:
    settings = settings or get_settings()
    started = datetime.now(timezone.utc)

    row = await db.fetchrow(
        """
        SELECT s.*, c.name AS company_name, c.careers_url, c.website, c.notes
        FROM sources s
        JOIN companies c ON c.id = s.company_id
        WHERE s.id = $1
        """,
        source_id,
    )
    if not row:
        return RefreshOutcome(
            source_id=source_id,
            company_id=0,
            company_name="?",
            ok=False,
            error="source not found",
        )

    company = Company(
        id=row["company_id"],
        name=row["company_name"],
        careers_url=row["careers_url"],
        website=row["website"],
        notes=row["notes"],
    )
    source = _source_from_row(row)

    await bus.publish(
        "refresh_progress",
        {
            "run_id": run_id,
            "source_id": source.id,
            "company_id": company.id,
            "company_name": company.name,
            "status": "started",
        },
    )

    try:
        connector = registry.get_or_generic(source.ats_type)
        result = await connector.list_jobs(company, source, client)

        if result.fallback_stage or result.needs_playwright is not None:
            await db.execute(
                """
                UPDATE sources SET
                    fallback_stage = COALESCE($2, fallback_stage),
                    needs_playwright = COALESCE($3, needs_playwright),
                    updated_at = now()
                WHERE id = $1
                """,
                source.id,
                result.fallback_stage,
                result.needs_playwright,
            )

        if not result.complete:
            stage = result.fallback_stage or source.fallback_stage or "unknown"
            # One-shot redetect for misclassified generic/unknown sources
            if (
                not source.ats_override
                and source.ats_type in {"generic", "unknown"}
                and not (company.careers_url or "").startswith("portal://")
            ):
                try:
                    from app.detect import detect_ats

                    detected = await detect_ats(company.careers_url, client, company.name)
                    if detected.ats_type not in {"generic", "unknown"} and detected.ats_type != source.ats_type:
                        await db.execute(
                            """
                            UPDATE sources SET
                                ats_type = $2,
                                ats_slug = COALESCE($3, ats_slug),
                                detect_confidence = $4,
                                fallback_stage = $5,
                                needs_playwright = $6,
                                baseline_completed = false,
                                last_fingerprint = NULL,
                                last_etag = NULL,
                                last_modified = NULL,
                                failure_count = 0,
                                last_error = $7,
                                next_poll_at = now(),
                                updated_at = now()
                            WHERE id = $1
                            """,
                            source.id,
                            detected.ats_type,
                            detected.ats_slug,
                            detected.confidence,
                            detected.fallback_stage,
                            detected.needs_playwright,
                            f"incomplete list fetch ({stage}); redetected as {detected.ats_type}",
                        )
                        outcome = RefreshOutcome(
                            source_id=source.id,
                            company_id=company.id,
                            company_name=company.name,
                            ok=False,
                            error=f"incomplete list fetch; redetected as {detected.ats_type}",
                            duration_ms=_ms(started),
                        )
                        await _publish_done(run_id, outcome)
                        return outcome
                except Exception as exc:
                    log.warning("auto-redetect failed for %s: %s", company.name, exc)

            err = f"incomplete list fetch ({stage})"
            await _record_soft_failure(source, err)
            outcome = RefreshOutcome(
                source_id=source.id,
                company_id=company.id,
                company_name=company.name,
                ok=False,
                error=err,
                duration_ms=_ms(started),
            )
            await _publish_done(run_id, outcome)
            return outcome

        # Conditional unchanged (304-style empty stubs with same fingerprint)
        if (
            not result.stubs
            and result.fingerprint
            and source.last_fingerprint
            and result.fingerprint == source.last_fingerprint
            and source.baseline_completed
        ):
            await _touch_success(source, result, jobs_found=0, adaptive=True)
            outcome = RefreshOutcome(
                source_id=source.id,
                company_id=company.id,
                company_name=company.name,
                ok=True,
                unchanged=True,
                duration_ms=_ms(started),
            )
            await _publish_done(run_id, outcome)
            return outcome

        if not source.baseline_completed:
            new_count = await _baseline_ingest(company, source, result, connector, client)
            await _mark_baseline_done(source, result, jobs_found=len(result.stubs))
            outcome = RefreshOutcome(
                source_id=source.id,
                company_id=company.id,
                company_name=company.name,
                ok=True,
                was_baseline=True,
                jobs_found=len(result.stubs),
                jobs_new=new_count,
                duration_ms=_ms(started),
            )
            await _publish_done(run_id, outcome)
            return outcome

        # Unchanged fingerprint — still bookkeeping for last_seen / miss counters
        if (
            result.fingerprint
            and source.last_fingerprint
            and result.fingerprint == source.last_fingerprint
        ):
            await _bump_last_seen(source.id, [s.external_job_id for s in result.stubs])
            await _touch_success(source, result, jobs_found=len(result.stubs), adaptive=True)
            outcome = RefreshOutcome(
                source_id=source.id,
                company_id=company.id,
                company_name=company.name,
                ok=True,
                unchanged=True,
                jobs_found=len(result.stubs),
                duration_ms=_ms(started),
            )
            await _publish_done(run_id, outcome)
            return outcome

        existing = await db.fetch(
            "SELECT id, external_job_id, content_hash, status FROM jobs WHERE source_id = $1",
            source.id,
        )
        by_ext = {r["external_job_id"]: r for r in existing}
        listed_ids = {s.external_job_id for s in result.stubs}

        new_stubs = [s for s in result.stubs if s.external_job_id not in by_ext]
        drafts = await _fetch_drafts(company, source, new_stubs, connector, client)

        new_job_ids = await _upsert_jobs(
            company, source, drafts, is_baseline=False, emit_events=True
        )
        if new_job_ids:
            await match_jobs_to_searches(new_job_ids)

        # Reset miss counters for present jobs; two-strike close for missing
        closed = await _apply_two_strike(source, listed_ids, settings.close_miss_threshold)

        await _touch_success(source, result, jobs_found=len(result.stubs), adaptive=True)

        outcome = RefreshOutcome(
            source_id=source.id,
            company_id=company.id,
            company_name=company.name,
            ok=True,
            jobs_found=len(result.stubs),
            jobs_new=len(new_job_ids),
            jobs_closed=closed,
            duration_ms=_ms(started),
        )
        await _publish_done(run_id, outcome)
        return outcome

    except Exception as exc:
        log.exception("refresh failed source=%s company=%s", source.id, company.name)
        await _record_failure(source, str(exc), settings)
        outcome = RefreshOutcome(
            source_id=source.id,
            company_id=company.id,
            company_name=company.name,
            ok=False,
            error=str(exc),
            duration_ms=_ms(started),
        )
        await _publish_done(run_id, outcome)
        return outcome


async def _baseline_ingest(
    company: Company,
    source: Source,
    result: ListResult,
    connector: Any,
    client: HttpClient,
) -> int:
    drafts = await _fetch_drafts(company, source, result.stubs, connector, client)
    ids = await _upsert_jobs(company, source, drafts, is_baseline=True, emit_events=False)
    return len(ids)


def _draft_from_stub(stub: JobStub) -> JobDraft | None:
    """Build a JobDraft from list payload when it already has enough content."""
    from app.connectors.util import html_to_text, parse_datetime

    raw = stub.raw or {}
    html = raw.get("content") or raw.get("description") or raw.get("descriptionHtml") or ""
    text = raw.get("descriptionPlain") or ""
    if not text and isinstance(html, str) and len(html) > 40:
        text = html_to_text(html)
    if not text and not html:
        return None
    posted = (
        stub.updated_at
        or parse_datetime(raw.get("updated_at"))
        or parse_datetime(raw.get("created_at"))
        or parse_datetime(raw.get("createdAt"))
        or parse_datetime(raw.get("publishedAt"))
        or parse_datetime(raw.get("postedOn"))
    )
    return JobDraft(
        external_job_id=stub.external_job_id,
        title=stub.title,
        url=stub.url or stub.absolute_url,
        apply_url=stub.url
        or stub.absolute_url
        or raw.get("absolute_url")
        or raw.get("applyUrl")
        or raw.get("hostedUrl"),
        location_raw=stub.location_raw,
        description_html=html if isinstance(html, str) else None,
        description_text=text or None,
        content_hash=content_hash(text or stub.title),
        posted_at=posted,
        raw=raw,
    )


async def _fetch_drafts(
    company: Company,
    source: Source,
    stubs: list[JobStub],
    connector: Any,
    client: HttpClient,
) -> list[JobDraft]:
    import asyncio

    settings = get_settings()
    # Drop obvious non-tech titles before expensive detail fetches (Workday/Oracle).
    if settings.tech_roles_only:
        kept: list[JobStub] = []
        for stub in stubs:
            if is_tech_role(stub.title):
                kept.append(stub)
            else:
                continue
        skipped = len(stubs) - len(kept)
        if skipped:
            log.info("Pre-filtered %s non-tech list rows for %s", skipped, company.name)
        stubs = kept

    sem = asyncio.Semaphore(settings.detail_concurrency)
    drafts: list[JobDraft | None] = [None] * len(stubs)

    async def _one(idx: int, stub: JobStub) -> None:
        # Fast path: Greenhouse/Lever list already included content
        quick = _draft_from_stub(stub)
        if quick is not None:
            drafts[idx] = _enrich_draft(quick)
            return
        # Oracle list payload already includes secondaryLocations — no detail needed.
        # Workday must always hit detail: additionalLocations are not on the list API.
        if source.ats_type == "oracle" and stub.title and stub.location_raw:
            drafts[idx] = _enrich_draft(
                JobDraft(
                    external_job_id=stub.external_job_id,
                    title=stub.title,
                    url=stub.url,
                    apply_url=stub.url,
                    location_raw=stub.location_raw,
                    description_text=None,
                    content_hash=content_hash(stub.title),
                    posted_at=stub.updated_at,
                    raw=stub.raw,
                )
            )
            return
        async with sem:
            try:
                draft = await connector.get_job(company, source, stub, client)
            except Exception as exc:
                log.warning(
                    "get_job failed %s/%s: %s — using stub",
                    company.name,
                    stub.external_job_id,
                    exc,
                )
                draft = JobDraft(
                    external_job_id=stub.external_job_id,
                    title=stub.title,
                    url=stub.url,
                    apply_url=stub.url,
                    location_raw=stub.location_raw,
                    description_text=None,
                    content_hash=content_hash(stub.title),
                    posted_at=stub.updated_at,
                    raw=stub.raw,
                )
            drafts[idx] = _enrich_draft(draft)

    await asyncio.gather(*[_one(i, s) for i, s in enumerate(stubs)])
    return [d for d in drafts if d is not None]


def _enrich_draft(draft: JobDraft) -> JobDraft:
    parsed = parse_jd(draft.title, draft.description_text, draft.location_raw)
    loc = normalize_location(draft.location_raw)

    if draft.yoe_min is None:
        draft.yoe_min = parsed.yoe_min
    if draft.yoe_max is None:
        draft.yoe_max = parsed.yoe_max
    if draft.workplace_type == "unknown":
        draft.workplace_type = loc.workplace_hint or parsed.workplace_type
    if not draft.seniority:
        draft.seniority = parsed.seniority
    if not draft.skills:
        draft.skills = parsed.skills or []
    if not draft.city:
        draft.city = loc.city
    if not draft.state:
        draft.state = loc.state
    if not draft.country:
        draft.country = loc.country
    if not draft.content_hash:
        draft.content_hash = content_hash(draft.description_text or draft.title)
    if not draft.apply_url:
        draft.apply_url = draft.url
    if not draft.source_portal and isinstance(draft.raw, dict):
        portal = draft.raw.get("portal")
        if portal:
            draft.source_portal = str(portal)
    if not draft.employer_name and isinstance(draft.raw, dict):
        employer = draft.raw.get("employer") or draft.raw.get("companyName")
        if employer:
            draft.employer_name = str(employer)
        elif draft.department and draft.source_portal:
            draft.employer_name = draft.department
    return draft


async def _upsert_jobs(
    company: Company,
    source: Source,
    drafts: list[JobDraft],
    *,
    is_baseline: bool,
    emit_events: bool,
) -> list[int]:
    settings = get_settings()
    job_ids: list[int] = []
    skipped_non_tech = 0
    for draft in drafts:
        if settings.tech_roles_only and not is_tech_role(
            draft.title,
            department=draft.department,
            description_text=draft.description_text,
        ):
            skipped_non_tech += 1
            continue
        if not looks_like_job_url(draft.apply_url or draft.url):
            skipped_non_tech += 1
            continue
        loc = primary_location(draft.location_raw)
        # Prefer draft scalars only when normalizer found nothing useful
        city = loc.city or draft.city
        state = loc.state or draft.state
        country = loc.country or draft.country
        locs = scrub_obj(locations_payload(draft.location_raw))
        workplace = draft.workplace_type
        if workplace == "unknown" and loc.workplace_hint:
            workplace = loc.workplace_hint

        row = await db.fetchrow(
            """
            INSERT INTO jobs (
                company_id, source_id, external_job_id, title, normalized_title,
                url, apply_url, location_raw, city, state, country, city_normalized,
                workplace_type, employment_type, department, seniority,
                yoe_min, yoe_max, salary_min, salary_max, salary_currency,
                description_text, description_html, skills, content_hash, raw,
                posted_at, first_seen_at, last_seen_at, status, is_baseline,
                missing_poll_count, source_portal, employer_name, is_tech, locations
            ) VALUES (
                $1,$2,$3,$4,$5,
                $6,$7,$8,$9,$10,$11,$12,
                $13,$14,$15,$16,
                $17,$18,$19,$20,$21,
                $22,$23,$24,$25,$26,
                $27, now(), now(), 'open', $28,
                0, $29, $30, true, $31::jsonb
            )
            ON CONFLICT (company_id, external_job_id) DO UPDATE SET
                title = EXCLUDED.title,
                normalized_title = EXCLUDED.normalized_title,
                url = COALESCE(EXCLUDED.url, jobs.url),
                apply_url = COALESCE(EXCLUDED.apply_url, jobs.apply_url),
                location_raw = CASE
                    WHEN EXCLUDED.location_raw IS NULL THEN jobs.location_raw
                    WHEN jobs.location_raw IS NULL THEN EXCLUDED.location_raw
                    WHEN jobs.location_raw ~* '^[0-9]+[[:space:]]+locations?$' THEN EXCLUDED.location_raw
                    WHEN strpos(EXCLUDED.location_raw, ';') > 0 THEN EXCLUDED.location_raw
                    WHEN length(EXCLUDED.location_raw) >= length(jobs.location_raw) THEN EXCLUDED.location_raw
                    ELSE COALESCE(EXCLUDED.location_raw, jobs.location_raw)
                END,
                city = COALESCE(EXCLUDED.city, jobs.city),
                state = COALESCE(EXCLUDED.state, jobs.state),
                country = COALESCE(EXCLUDED.country, jobs.country),
                city_normalized = COALESCE(EXCLUDED.city_normalized, jobs.city_normalized),
                locations = CASE
                    WHEN jsonb_array_length(EXCLUDED.locations) >= jsonb_array_length(jobs.locations)
                         AND EXCLUDED.locations <> '[]'::jsonb
                        THEN EXCLUDED.locations
                    WHEN EXCLUDED.locations <> '[]'::jsonb AND jobs.locations = '[]'::jsonb
                        THEN EXCLUDED.locations
                    ELSE jobs.locations
                END,
                workplace_type = CASE
                    WHEN EXCLUDED.workplace_type <> 'unknown' THEN EXCLUDED.workplace_type
                    ELSE jobs.workplace_type END,
                employment_type = COALESCE(EXCLUDED.employment_type, jobs.employment_type),
                department = COALESCE(EXCLUDED.department, jobs.department),
                seniority = COALESCE(EXCLUDED.seniority, jobs.seniority),
                yoe_min = COALESCE(EXCLUDED.yoe_min, jobs.yoe_min),
                yoe_max = COALESCE(EXCLUDED.yoe_max, jobs.yoe_max),
                description_text = COALESCE(EXCLUDED.description_text, jobs.description_text),
                description_html = COALESCE(EXCLUDED.description_html, jobs.description_html),
                skills = CASE WHEN cardinality(EXCLUDED.skills) > 0 THEN EXCLUDED.skills ELSE jobs.skills END,
                content_hash = COALESCE(EXCLUDED.content_hash, jobs.content_hash),
                raw = CASE WHEN EXCLUDED.raw <> '{}'::jsonb THEN EXCLUDED.raw ELSE jobs.raw END,
                posted_at = COALESCE(EXCLUDED.posted_at, jobs.posted_at),
                source_portal = COALESCE(EXCLUDED.source_portal, jobs.source_portal),
                employer_name = COALESCE(EXCLUDED.employer_name, jobs.employer_name),
                is_tech = true,
                last_seen_at = now(),
                status = 'open',
                closed_at = NULL,
                missing_poll_count = 0,
                updated_at = now()
            RETURNING id, (xmax = 0) AS inserted
            """,
            company.id,
            source.id,
            scrub_text(draft.external_job_id) or "",
            scrub_text(draft.title) or "",
            scrub_text(normalize_title(draft.title)) or "",
            scrub_text(draft.url),
            scrub_text(draft.apply_url),
            scrub_text(draft.location_raw),
            scrub_text(city),
            scrub_text(state),
            scrub_text(country),
            scrub_text(loc.city_normalized or city),
            workplace,
            scrub_text(draft.employment_type),
            scrub_text(draft.department),
            scrub_text(draft.seniority),
            draft.yoe_min,
            draft.yoe_max,
            draft.salary_min,
            draft.salary_max,
            scrub_text(draft.salary_currency),
            scrub_text(draft.description_text),
            scrub_text(draft.description_html),
            [scrub_text(s) or s for s in (draft.skills or [])],
            scrub_text(draft.content_hash),
            scrub_obj(draft.raw or {}),
            draft.posted_at,
            is_baseline,
            scrub_text(draft.source_portal),
            scrub_text(draft.employer_name),
            locs,
        )

        job_ids.append(row["id"])
        if emit_events and row["inserted"]:
            await db.execute(
                "INSERT INTO job_events (job_id, event_type, detail) VALUES ($1, 'seen', $2)",
                row["id"],
                {},
            )
            await bus.publish(
                "job_new",
                {
                    "job_id": row["id"],
                    "company_id": company.id,
                    "company_name": company.name,
                    "title": draft.title,
                    "is_baseline": False,
                },
            )
    if skipped_non_tech:
        log.info(
            "Skipped %s non-tech roles for %s",
            skipped_non_tech,
            company.name,
        )
    return job_ids


async def _apply_two_strike(source: Source, listed_ids: set[str], threshold: int) -> int:
    open_jobs = await db.fetch(
        "SELECT id, external_job_id, missing_poll_count FROM jobs WHERE source_id = $1 AND status = 'open'",
        source.id,
    )
    closed = 0
    for job in open_jobs:
        if job["external_job_id"] in listed_ids:
            if job["missing_poll_count"]:
                await db.execute(
                    "UPDATE jobs SET missing_poll_count = 0, last_seen_at = now(), updated_at = now() WHERE id = $1",
                    job["id"],
                )
            else:
                await db.execute(
                    "UPDATE jobs SET last_seen_at = now() WHERE id = $1",
                    job["id"],
                )
            continue

        new_count = int(job["missing_poll_count"] or 0) + 1
        if new_count >= threshold:
            await db.execute(
                """
                UPDATE jobs
                SET status = 'closed', closed_at = now(), missing_poll_count = $2, updated_at = now()
                WHERE id = $1
                """,
                job["id"],
                new_count,
            )
            await db.execute(
                "INSERT INTO job_events (job_id, event_type) VALUES ($1, 'closed')",
                job["id"],
            )
            await bus.publish("job_closed", {"job_id": job["id"], "source_id": source.id})
            closed += 1
        else:
            await db.execute(
                "UPDATE jobs SET missing_poll_count = $2, updated_at = now() WHERE id = $1",
                job["id"],
                new_count,
            )
    return closed


async def _bump_last_seen(source_id: int, external_ids: list[str]) -> None:
    if not external_ids:
        return
    await db.execute(
        """
        UPDATE jobs
        SET last_seen_at = now(), missing_poll_count = 0, updated_at = now()
        WHERE source_id = $1 AND external_job_id = ANY($2::text[])
        """,
        source_id,
        external_ids,
    )


async def _mark_baseline_done(source: Source, result: ListResult, jobs_found: int) -> None:
    interval = _adaptive_interval(0)
    await db.execute(
        """
        UPDATE sources SET
            baseline_completed = true,
            baseline_completed_at = now(),
            last_checked_at = now(),
            last_success_at = now(),
            last_etag = $2,
            last_modified = $3,
            last_fingerprint = $4,
            failure_count = 0,
            last_error = NULL,
            circuit_open_until = NULL,
            poll_interval_seconds = $5,
            next_poll_at = now() + make_interval(secs => $6::double precision),
            updated_at = now()
        WHERE id = $1
        """,
        source.id,
        result.etag,
        result.last_modified,
        result.fingerprint,
        interval,
        float(interval),
    )


async def _touch_success(
    source: Source, result: ListResult, *, jobs_found: int, adaptive: bool
) -> None:
    new_rate = await db.fetchval(
        """
        SELECT COUNT(*) FROM jobs
        WHERE source_id = $1 AND is_baseline = false
          AND first_seen_at > now() - interval '7 days'
        """,
        source.id,
    )
    interval = _adaptive_interval(int(new_rate or 0)) if adaptive else source.poll_interval_seconds
    await db.execute(
        """
        UPDATE sources SET
            last_checked_at = now(),
            last_success_at = now(),
            last_etag = COALESCE($2, last_etag),
            last_modified = COALESCE($3, last_modified),
            last_fingerprint = COALESCE($4, last_fingerprint),
            failure_count = 0,
            last_error = NULL,
            circuit_open_until = NULL,
            poll_interval_seconds = $5,
            next_poll_at = now() + make_interval(secs => $6::double precision),
            updated_at = now()
        WHERE id = $1
        """,
        source.id,
        result.etag,
        result.last_modified,
        result.fingerprint,
        interval,
        float(interval),
    )


async def _record_soft_failure(source: Source, error: str) -> None:
    backoff = float(min((source.poll_interval_seconds or 1800) * 2, 21600))
    await db.execute(
        """
        UPDATE sources SET
            last_checked_at = now(),
            last_error = $2,
            failure_count = failure_count + 1,
            next_poll_at = now() + make_interval(secs => $3::double precision),
            updated_at = now()
        WHERE id = $1
        """,
        source.id,
        scrub_text(error[:2000]) or error[:2000],
        backoff,
    )


async def _record_failure(source: Source, error: str, settings: Settings) -> None:
    failures = source.failure_count + 1
    circuit_until = None
    backoff = float(min((source.poll_interval_seconds or 1800) * 2, 21600))
    if failures >= 5:
        cooldown = min(6 * 3600, 15 * 60 * (2 ** max(0, failures - 5)))
        circuit_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown)
    await db.execute(
        """
        UPDATE sources SET
            last_checked_at = now(),
            last_error = $2,
            failure_count = $3,
            circuit_open_until = $4,
            next_poll_at = COALESCE($4, now() + make_interval(secs => $5::double precision)),
            updated_at = now()
        WHERE id = $1
        """,
        source.id,
        scrub_text(error[:2000]) or error[:2000],
        failures,
        circuit_until,
        backoff,
    )


def _adaptive_interval(new_jobs_7d: int) -> int:
    if new_jobs_7d >= 5:
        base = 300
    elif new_jobs_7d >= 1:
        base = 1200
    else:
        base = 7200
    jitter = random.uniform(0.85, 1.15)
    return int(max(180, min(21600, base * jitter)))


async def _publish_done(run_id: int | None, outcome: RefreshOutcome) -> None:
    await bus.publish(
        "refresh_progress",
        {
            "run_id": run_id,
            "source_id": outcome.source_id,
            "company_id": outcome.company_id,
            "company_name": outcome.company_name,
            "status": "done" if outcome.ok else "error",
            "was_baseline": outcome.was_baseline,
            "jobs_found": outcome.jobs_found,
            "jobs_new": outcome.jobs_new,
            "jobs_closed": outcome.jobs_closed,
            "error": outcome.error,
            "duration_ms": outcome.duration_ms,
            "unchanged": outcome.unchanged,
        },
    )


def _source_from_row(row: Any) -> Source:
    return Source(
        id=row["id"],
        company_id=row["company_id"],
        ats_type=row["ats_type"],
        ats_slug=row["ats_slug"],
        ats_override=row["ats_override"],
        poll_interval_seconds=row["poll_interval_seconds"],
        next_poll_at=row["next_poll_at"],
        last_checked_at=row["last_checked_at"],
        last_success_at=row["last_success_at"],
        last_etag=row["last_etag"],
        last_modified=row["last_modified"],
        last_fingerprint=row["last_fingerprint"],
        failure_count=row["failure_count"],
        last_error=row["last_error"],
        circuit_open_until=row["circuit_open_until"],
        baseline_completed=row["baseline_completed"],
        baseline_completed_at=row["baseline_completed_at"],
        robots_allowed=row["robots_allowed"],
        detect_confidence=row["detect_confidence"],
        fallback_stage=row["fallback_stage"],
        needs_playwright=row["needs_playwright"],
    )


def _ms(started: datetime) -> int:
    return int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

