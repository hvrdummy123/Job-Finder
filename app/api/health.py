from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app import db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    await db.fetchval("SELECT 1")
    return {"status": "ok"}


@router.get("/sources/health")
async def sources_health() -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        SELECT
            s.id AS source_id,
            c.id AS company_id,
            c.name AS company_name,
            c.careers_url,
            s.ats_type,
            s.ats_slug,
            s.ats_override,
            s.fallback_stage,
            s.needs_playwright,
            s.baseline_completed,
            s.baseline_completed_at,
            s.poll_interval_seconds,
            s.next_poll_at,
            s.last_checked_at,
            s.last_success_at,
            s.failure_count,
            s.last_error,
            s.circuit_open_until,
            (SELECT COUNT(*) FROM jobs j WHERE j.source_id = s.id AND j.status = 'open') AS open_jobs
        FROM sources s
        JOIN companies c ON c.id = s.company_id
        ORDER BY
            s.needs_playwright DESC,
            s.failure_count DESC,
            c.name ASC
        """
    )
    return [dict(r) for r in rows]
