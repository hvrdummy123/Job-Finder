from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.data.role_titles import ROLE_TITLE_SEED

router = APIRouter(prefix="/role-titles", tags=["role-titles"])


class ActiveTitlesPatch(BaseModel):
    title_ids: list[int] = Field(default_factory=list)
    filter_enabled: bool | None = None


class CatalogCreate(BaseModel):
    title: str
    category: str = "custom"
    aliases: list[str] = Field(default_factory=list)


async def ensure_catalog_seeded() -> None:
    count = await db.fetchval("SELECT COUNT(*) FROM role_title_catalog")
    if count and int(count) > 0:
        return
    for title, category, aliases, sort_order in ROLE_TITLE_SEED:
        await db.execute(
            """
            INSERT INTO role_title_catalog (title, category, aliases, sort_order)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (title) DO NOTHING
            """,
            title,
            category,
            aliases,
            sort_order,
        )


@router.get("")
async def list_role_titles() -> dict[str, Any]:
    await ensure_catalog_seeded()
    rows = await db.fetch(
        """
        SELECT c.id, c.title, c.category, c.aliases, c.sort_order,
               (a.title_id IS NOT NULL) AS active
        FROM role_title_catalog c
        LEFT JOIN user_active_role_titles a ON a.title_id = c.id
        ORDER BY c.sort_order, c.title
        """
    )
    settings = await db.fetchrow("SELECT filter_enabled FROM role_title_settings WHERE id = 1")
    active = [dict(r) for r in rows if r["active"]]
    return {
        "filter_enabled": bool(settings["filter_enabled"]) if settings else False,
        "catalog": [dict(r) for r in rows],
        "active": active,
        "active_count": len(active),
        "catalog_count": len(rows),
    }


@router.put("/active")
async def set_active_titles(body: ActiveTitlesPatch) -> dict[str, Any]:
    await ensure_catalog_seeded()
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM user_active_role_titles")
            for tid in body.title_ids:
                await conn.execute(
                    """
                    INSERT INTO user_active_role_titles (title_id)
                    SELECT $1 WHERE EXISTS (SELECT 1 FROM role_title_catalog WHERE id = $1)
                    ON CONFLICT DO NOTHING
                    """,
                    tid,
                )
            if body.filter_enabled is not None:
                await conn.execute(
                    """
                    INSERT INTO role_title_settings (id, filter_enabled, updated_at)
                    VALUES (1, $1, now())
                    ON CONFLICT (id) DO UPDATE SET
                        filter_enabled = EXCLUDED.filter_enabled,
                        updated_at = now()
                    """,
                    body.filter_enabled,
                )
    return await list_role_titles()


@router.post("/active/{title_id}")
async def add_active_title(title_id: int) -> dict[str, Any]:
    exists = await db.fetchval("SELECT 1 FROM role_title_catalog WHERE id = $1", title_id)
    if not exists:
        raise HTTPException(404, "Title not in catalog")
    await db.execute(
        """
        INSERT INTO user_active_role_titles (title_id) VALUES ($1)
        ON CONFLICT DO NOTHING
        """,
        title_id,
    )
    return await list_role_titles()


@router.delete("/active/{title_id}")
async def remove_active_title(title_id: int) -> dict[str, Any]:
    await db.execute("DELETE FROM user_active_role_titles WHERE title_id = $1", title_id)
    return await list_role_titles()


@router.post("/catalog")
async def create_catalog_title(body: CatalogCreate) -> dict[str, Any]:
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "title required")
    row = await db.fetchrow(
        """
        INSERT INTO role_title_catalog (title, category, aliases, sort_order)
        VALUES ($1, $2, $3, 500)
        ON CONFLICT (title) DO UPDATE SET
            category = EXCLUDED.category,
            aliases = CASE
                WHEN cardinality(EXCLUDED.aliases) > 0 THEN EXCLUDED.aliases
                ELSE role_title_catalog.aliases
            END
        RETURNING *
        """,
        title,
        body.category.strip() or "custom",
        body.aliases,
    )
    return dict(row)


@router.post("/settings")
async def update_settings(body: ActiveTitlesPatch) -> dict[str, Any]:
    if body.filter_enabled is None and not body.title_ids:
        raise HTTPException(400, "Nothing to update")
    if body.filter_enabled is not None:
        await db.execute(
            """
            INSERT INTO role_title_settings (id, filter_enabled, updated_at)
            VALUES (1, $1, now())
            ON CONFLICT (id) DO UPDATE SET
                filter_enabled = EXCLUDED.filter_enabled,
                updated_at = now()
            """,
            body.filter_enabled,
        )
    if body.title_ids:
        return await set_active_titles(body)
    return await list_role_titles()
