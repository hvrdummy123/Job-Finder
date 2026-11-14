from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app import db

router = APIRouter(prefix="/visits", tags=["visits"])


@router.get("")
async def get_visit() -> dict[str, Any]:
    row = await db.fetchrow("SELECT * FROM user_visits WHERE id = 1")
    return dict(row) if row else {"last_visit_at": None}


@router.post("")
async def touch_visit() -> dict[str, Any]:
    row = await db.fetchrow(
        """
        INSERT INTO user_visits (id, last_visit_at) VALUES (1, now())
        ON CONFLICT (id) DO UPDATE SET last_visit_at = now()
        RETURNING *
        """
    )
    return dict(row)
