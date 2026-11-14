from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import db
from app.detect import detect_ats
from app.ingest.role_filter import is_tech_role, looks_like_job_url

router = APIRouter(prefix="/companies", tags=["companies"])

KNOWN_ATS = {
    "greenhouse",
    "lever",
    "ashby",
    "workday",
    "amazon",
    "smartrecruiters",
    "oracle",
    "generic",
    "indeed",
    "wellfound",
    "naukri",
    "rocketship",
    "unknown",
}


class CompanyIn(BaseModel):
    name: str
    careers_url: str
    website: str | None = None
    notes: str | None = None
    categories: list[str] = Field(default_factory=list)
    ats_type: str | None = None
    ats_slug: str | None = None
    refresh: bool = True


class CompanyPatch(BaseModel):
    name: str | None = None
    careers_url: str | None = None
    website: str | None = None
    notes: str | None = None
    categories: list[str] | None = None
    ats_type: str | None = None
    ats_slug: str | None = None
    ats_override: bool | None = None


class FixSourcesBody(BaseModel):
    redetect: bool = True
    clear_stale_errors: bool = True
    purge_junk: bool = True
    refresh: bool = True
    company_ids: list[int] | None = None


@router.get("")
async def list_companies() -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        SELECT c.*,
               s.id AS source_id,
               s.ats_type,
               s.ats_slug,
               s.ats_override,
               s.baseline_completed,
               s.fallback_stage,
               s.needs_playwright,
               s.last_success_at,
               s.last_checked_at,
               s.last_error,
               s.poll_interval_seconds,
               s.failure_count,
               s.circuit_open_until,
               (SELECT COUNT(*)::int FROM jobs j
                 WHERE j.company_id = c.id AND j.status = 'open' AND j.is_tech = true) AS open_jobs
        FROM companies c
        LEFT JOIN sources s ON s.company_id = c.id
        ORDER BY c.name
        """
    )
    return [dict(r) for r in rows]


@router.post("")
async def create_company(body: CompanyIn, request: Request) -> dict[str, Any]:
    client = request.app.state.http
    detected = None
    if not body.ats_type:
        detected = await detect_ats(body.careers_url, client, body.name)

    ats_type = body.ats_type or (detected.ats_type if detected else "unknown")
    if ats_type not in KNOWN_ATS:
        ats_type = "generic"
    ats_slug = body.ats_slug or (detected.ats_slug if detected else None)
    confidence = detected.confidence if detected else None
    fallback = detected.fallback_stage if detected else "unknown"
    needs_pw = detected.needs_playwright if detected else False

    # portal://scheme short-circuit
    if body.careers_url.startswith("portal://"):
        portal = body.careers_url.split("://", 1)[1].split("/", 1)[0].replace("indeed-in", "indeed")
        ats_type = body.ats_type or portal
        if not ats_slug and "/" in body.careers_url.split("://", 1)[1]:
            ats_slug = body.careers_url.split("://", 1)[1].split("/", 1)[1] or ats_slug

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            company = await conn.fetchrow(
                """
                INSERT INTO companies (name, careers_url, website, notes, categories)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
                """,
                body.name.strip(),
                body.careers_url.strip(),
                body.website,
                body.notes,
                body.categories,
            )
            source = await conn.fetchrow(
                """
                INSERT INTO sources (
                    company_id, ats_type, ats_slug, ats_override,
                    detect_confidence, fallback_stage, needs_playwright,
                    next_poll_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, now())
                RETURNING *
                """,
                company["id"],
                ats_type,
                ats_slug,
                bool(body.ats_type) or body.careers_url.startswith("portal://"),
                confidence,
                fallback,
                needs_pw,
            )

    run_id = None
    if body.refresh:
        try:
            run_id = await request.app.state.scheduler.start_refresh_company(company["id"])
        except Exception:
            run_id = None

    return {**dict(company), "source": dict(source), "refresh_run_id": run_id}


@router.post("/fix-sources")
async def fix_sources(body: FixSourcesBody, request: Request) -> dict[str, Any]:
    """Resolve common source failures: stale type errors, junk jobs, wrong ATS."""
    purged = 0
    cleared = 0
    redetected = 0
    refreshed: list[int] = []

    if body.purge_junk:
        purged = await _purge_junk_jobs()

    if body.clear_stale_errors:
        result = await db.execute(
            """
            UPDATE sources SET
                last_error = NULL,
                failure_count = 0,
                circuit_open_until = NULL,
                next_poll_at = now(),
                updated_at = now()
            WHERE last_error ILIKE '%inconsistent types deduced%'
               OR last_error ILIKE '%UTF8%: 0x00%'
               OR last_error ILIKE '%invalid byte sequence%'
            """
        )
        try:
            cleared = int(str(result).split()[-1])
        except Exception:
            cleared = 0

    # Clear accidental overrides (e.g. from --skip-detect seed) so redetect can run.
    # Keep intentional portal / explicit ATS overrides.
    cleared_overrides = await db.fetchval(
        """
        WITH updated AS (
            UPDATE sources s SET
                ats_override = false,
                updated_at = now()
            FROM companies c
            WHERE c.id = s.company_id
              AND s.ats_override = true
              AND c.careers_url NOT LIKE 'portal://%'
              AND s.ats_type NOT IN ('indeed', 'wellfound', 'naukri', 'rocketship')
              AND (
                s.last_error ILIKE '%incomplete list%'
                OR s.ats_type IN ('generic', 'unknown')
                OR s.needs_playwright = true
                OR s.failure_count >= 2
              )
            RETURNING s.id
        )
        SELECT COUNT(*)::int FROM updated
        """
    )

    target_ids = body.company_ids
    if target_ids is None:
        rows = await db.fetch(
            """
            SELECT c.id, c.name, c.careers_url, s.ats_type, s.ats_override, s.last_error, s.fallback_stage
            FROM companies c
            JOIN sources s ON s.company_id = c.id
            WHERE c.careers_url NOT LIKE 'portal://%'
              AND (
                s.ats_override = false
                OR s.last_error ILIKE '%incomplete list%'
                OR s.ats_type IN ('generic', 'unknown')
              )
              AND (
                s.last_error ILIKE '%incomplete list%'
                OR s.ats_type IN ('generic', 'unknown')
                OR s.needs_playwright = true
                OR s.failure_count >= 2
                OR COALESCE((SELECT COUNT(*) FROM jobs j WHERE j.company_id = c.id AND j.status = 'open'), 0) = 0
              )
            ORDER BY s.failure_count DESC, c.name
            LIMIT 600
            """
        )
    else:
        rows = await db.fetch(
            """
            SELECT c.id, c.name, c.careers_url, s.ats_type, s.ats_override, s.last_error, s.fallback_stage
            FROM companies c
            JOIN sources s ON s.company_id = c.id
            WHERE c.id = ANY($1::bigint[])
            """,
            target_ids,
        )

    client = request.app.state.http
    for row in rows:
        is_portal = str(row["careers_url"]).startswith("portal://")
        can_redetect = body.redetect and not is_portal and (
            not row["ats_override"]
            or row["ats_type"] in {"generic", "unknown"}
            or (row["last_error"] and "incomplete list" in str(row["last_error"]))
        )
        if can_redetect:
            try:
                detected = await detect_ats(row["careers_url"], client, row["name"])
                await db.execute(
                    """
                    UPDATE sources SET
                        ats_type = $2,
                        ats_slug = COALESCE($3, ats_slug),
                        ats_override = false,
                        detect_confidence = $4,
                        fallback_stage = $5,
                        needs_playwright = $6,
                        failure_count = 0,
                        last_error = NULL,
                        circuit_open_until = NULL,
                        baseline_completed = false,
                        last_fingerprint = NULL,
                        last_etag = NULL,
                        last_modified = NULL,
                        next_poll_at = now(),
                        updated_at = now()
                    WHERE company_id = $1
                    """,
                    row["id"],
                    detected.ats_type,
                    detected.ats_slug,
                    detected.confidence,
                    detected.fallback_stage,
                    detected.needs_playwright,
                )
                redetected += 1
            except Exception:
                await db.execute(
                    """
                    UPDATE sources SET
                        failure_count = 0,
                        circuit_open_until = NULL,
                        next_poll_at = now(),
                        updated_at = now()
                    WHERE company_id = $1
                    """,
                    row["id"],
                )
        else:
            await db.execute(
                """
                UPDATE sources SET
                    failure_count = 0,
                    circuit_open_until = NULL,
                    next_poll_at = now(),
                    updated_at = now()
                WHERE company_id = $1
                """,
                row["id"],
            )

        refreshed.append(int(row["id"]))

    run_id = None
    if body.refresh and refreshed:
        # One parallel refresh for all fixed sources (avoid N priority tasks)
        try:
            run_id = await request.app.state.scheduler.start_refresh_all()
        except Exception:
            run_id = None

    return {
        "purged_junk_jobs": purged,
        "cleared_stale_errors": cleared,
        "cleared_overrides": int(cleared_overrides or 0),
        "redetected": redetected,
        "refresh_queued": len(refreshed) if body.refresh else 0,
        "refresh_run_id": run_id,
        "company_ids": refreshed,
    }


@router.post("/purge-junk")
async def purge_junk() -> dict[str, int]:
    purged = await _purge_junk_jobs()
    return {"purged": purged}


@router.patch("/{company_id}")
async def patch_company(company_id: int, body: CompanyPatch) -> dict[str, Any]:
    company = await db.fetchrow("SELECT * FROM companies WHERE id = $1", company_id)
    if not company:
        raise HTTPException(404, "Company not found")

    fields = body.model_dump(exclude_unset=True)
    ats_fields = {
        k: fields.pop(k) for k in list(fields) if k in {"ats_type", "ats_slug", "ats_override"}
    }

    if fields:
        sets = []
        args: list[Any] = []
        for key, value in fields.items():
            args.append(value)
            sets.append(f"{key} = ${len(args)}")
        args.append(company_id)
        sets.append("updated_at = now()")
        await db.execute(
            f"UPDATE companies SET {', '.join(sets)} WHERE id = ${len(args)}",
            *args,
        )

    if ats_fields:
        if "ats_type" in ats_fields:
            if ats_fields["ats_type"] not in KNOWN_ATS:
                raise HTTPException(400, f"Unknown ats_type: {ats_fields['ats_type']}")
            ats_fields.setdefault("ats_override", True)
            # New ATS → reset baseline so we re-ingest cleanly
            ats_fields["baseline_completed"] = False
            ats_fields["last_fingerprint"] = None
            ats_fields["failure_count"] = 0
            ats_fields["last_error"] = None
            ats_fields["circuit_open_until"] = None
            ats_fields["next_poll_at"] = None  # set via SQL now()
        sets = []
        args = []
        for key, value in ats_fields.items():
            if key == "next_poll_at" and value is None:
                sets.append("next_poll_at = now()")
                continue
            args.append(value)
            sets.append(f"{key} = ${len(args)}")
        args.append(company_id)
        sets.append("updated_at = now()")
        await db.execute(
            f"UPDATE sources SET {', '.join(sets)} WHERE company_id = ${len(args)}",
            *args,
        )

    rows = await db.fetch(
        """
        SELECT c.*, s.id AS source_id, s.ats_type, s.ats_slug, s.ats_override,
               s.baseline_completed, s.last_error, s.failure_count, s.fallback_stage,
               s.needs_playwright
        FROM companies c
        JOIN sources s ON s.company_id = c.id
        WHERE c.id = $1
        """,
        company_id,
    )
    return dict(rows[0])


@router.post("/{company_id}/detect")
async def redetect(company_id: int, request: Request) -> dict[str, Any]:
    company = await db.fetchrow("SELECT * FROM companies WHERE id = $1", company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    source = await db.fetchrow("SELECT * FROM sources WHERE company_id = $1", company_id)
    if source and source["ats_override"]:
        raise HTTPException(400, "ATS is manually overridden; clear override first")

    detected = await detect_ats(company["careers_url"], request.app.state.http, company["name"])
    row = await db.fetchrow(
        """
        UPDATE sources SET
            ats_type = $2,
            ats_slug = $3,
            detect_confidence = $4,
            fallback_stage = $5,
            needs_playwright = $6,
            failure_count = 0,
            last_error = NULL,
            circuit_open_until = NULL,
            baseline_completed = false,
            last_fingerprint = NULL,
            last_etag = NULL,
            last_modified = NULL,
            next_poll_at = now(),
            updated_at = now()
        WHERE company_id = $1
        RETURNING *
        """,
        company_id,
        detected.ats_type,
        detected.ats_slug,
        detected.confidence,
        detected.fallback_stage,
        detected.needs_playwright,
    )
    run_id = None
    try:
        run_id = await request.app.state.scheduler.start_refresh_company(company_id)
    except Exception:
        pass
    return {**dict(row), "refresh_run_id": run_id}


@router.delete("/{company_id}")
async def delete_company(company_id: int) -> dict[str, str]:
    result = await db.execute("DELETE FROM companies WHERE id = $1", company_id)
    if result == "DELETE 0":
        raise HTTPException(404, "Company not found")
    return {"status": "deleted"}


async def _purge_junk_jobs() -> int:
    """Close open jobs that are non-tech or don't look like real job URLs."""
    rows = await db.fetch(
        """
        SELECT id, title, url, apply_url, department, description_text
        FROM jobs
        WHERE status = 'open'
        """
    )
    junk_ids: list[int] = []
    for r in rows:
        url = r["apply_url"] or r["url"]
        if not looks_like_job_url(url):
            junk_ids.append(int(r["id"]))
            continue
        if not is_tech_role(
            r["title"],
            department=r["department"],
            description_text=r["description_text"],
        ):
            junk_ids.append(int(r["id"]))
    if not junk_ids:
        return 0
    # batch updates
    await db.execute(
        """
        UPDATE jobs SET
            status = 'closed',
            closed_at = COALESCE(closed_at, now()),
            updated_at = now(),
            is_tech = false
        WHERE id = ANY($1::bigint[]) AND status = 'open'
        """,
        junk_ids,
    )
    return len(junk_ids)
