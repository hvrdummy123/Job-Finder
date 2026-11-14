#!/usr/bin/env python3
"""Re-evaluate is_tech from titles and reopen jobs that now qualify."""

from __future__ import annotations

import asyncio
import sys

from app import db
from app.config import get_settings
from app.ingest.role_filter import is_tech_role, looks_like_job_url


async def main() -> int:
    settings = get_settings()
    await db.init_pool(settings)

    rows = await db.fetch(
        """
        SELECT id, title, url, apply_url, department, description_text, status, is_tech
        FROM jobs
        """
    )
    to_tech: list[int] = []
    to_non: list[int] = []
    reopen: list[int] = []

    for r in rows:
        ok = is_tech_role(
            r["title"],
            department=r["department"],
            description_text=r["description_text"],
        ) and looks_like_job_url(r["apply_url"] or r["url"])
        if ok and not r["is_tech"]:
            to_tech.append(int(r["id"]))
            if r["status"] == "closed":
                reopen.append(int(r["id"]))
        elif not ok and r["is_tech"]:
            to_non.append(int(r["id"]))

    if to_tech:
        await db.execute(
            "UPDATE jobs SET is_tech = true, updated_at = now() WHERE id = ANY($1::bigint[])",
            to_tech,
        )
    if reopen:
        await db.execute(
            """
            UPDATE jobs SET
                status = 'open',
                closed_at = NULL,
                missing_poll_count = 0,
                updated_at = now()
            WHERE id = ANY($1::bigint[]) AND status = 'closed'
            """,
            reopen,
        )
    if to_non:
        await db.execute(
            """
            UPDATE jobs SET
                is_tech = false,
                status = CASE WHEN status = 'open' THEN 'closed' ELSE status END,
                closed_at = CASE WHEN status = 'open' THEN COALESCE(closed_at, now()) ELSE closed_at END,
                updated_at = now()
            WHERE id = ANY($1::bigint[])
            """,
            to_non,
        )

    open_tech = await db.fetchval(
        "SELECT COUNT(*) FROM jobs WHERE status = 'open' AND is_tech = true"
    )
    print(
        f"marked_tech={len(to_tech)} reopened={len(reopen)} marked_non_tech={len(to_non)} "
        f"open_tech_now={open_tech}"
    )
    await db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
