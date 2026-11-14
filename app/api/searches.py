from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.ingest.match import backfill_saved_search

router = APIRouter(prefix="/saved-searches", tags=["saved-searches"])


class SavedSearchIn(BaseModel):
    name: str
    include_terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    yoe_min: int | None = None
    yoe_max: int | None = None
    include_unspecified_yoe: bool = True
    workplace_types: list[str] = Field(default_factory=list)
    is_active: bool = True


@router.get("")
async def list_searches() -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM job_search_matches m WHERE m.saved_search_id = s.id) AS match_count
        FROM saved_searches s
        ORDER BY s.created_at DESC
        """
    )
    return [dict(r) for r in rows]


@router.post("")
async def create_search(body: SavedSearchIn) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        INSERT INTO saved_searches (
            name, include_terms, exclude_terms, locations,
            yoe_min, yoe_max, include_unspecified_yoe, workplace_types, is_active
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        RETURNING *
        """,
        body.name,
        body.include_terms,
        body.exclude_terms,
        body.locations,
        body.yoe_min,
        body.yoe_max,
        body.include_unspecified_yoe,
        body.workplace_types,
        body.is_active,
    )
    asyncio.create_task(backfill_saved_search(row["id"]))
    return dict(row)


@router.patch("/{search_id}")
async def update_search(search_id: int, body: SavedSearchIn) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        UPDATE saved_searches SET
            name = $2,
            include_terms = $3,
            exclude_terms = $4,
            locations = $5,
            yoe_min = $6,
            yoe_max = $7,
            include_unspecified_yoe = $8,
            workplace_types = $9,
            is_active = $10,
            updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        search_id,
        body.name,
        body.include_terms,
        body.exclude_terms,
        body.locations,
        body.yoe_min,
        body.yoe_max,
        body.include_unspecified_yoe,
        body.workplace_types,
        body.is_active,
    )
    if not row:
        raise HTTPException(404, "Saved search not found")
    asyncio.create_task(backfill_saved_search(search_id))
    return dict(row)


@router.delete("/{search_id}")
async def delete_search(search_id: int) -> dict[str, str]:
    result = await db.execute("DELETE FROM saved_searches WHERE id = $1", search_id)
    if result == "DELETE 0":
        raise HTTPException(404, "Saved search not found")
    return {"status": "deleted"}


@router.get("/{search_id}/jobs")
async def search_jobs(search_id: int, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    exists = await db.fetchval("SELECT 1 FROM saved_searches WHERE id = $1", search_id)
    if not exists:
        raise HTTPException(404, "Saved search not found")
    rows = await db.fetch(
        """
        SELECT j.id, j.title, j.company_id, c.name AS company_name, j.location_raw,
               j.workplace_type, j.yoe_min, j.yoe_max, j.first_seen_at, j.posted_at,
               m.matched_at
        FROM job_search_matches m
        JOIN jobs j ON j.id = m.job_id
        JOIN companies c ON c.id = j.company_id
        WHERE m.saved_search_id = $1 AND j.status = 'open'
        ORDER BY m.matched_at DESC
        LIMIT $2 OFFSET $3
        """,
        search_id,
        limit,
        offset,
    )
    total = await db.fetchval(
        "SELECT COUNT(*) FROM job_search_matches WHERE saved_search_id = $1",
        search_id,
    )
    return {"total": total, "items": [dict(r) for r in rows]}
