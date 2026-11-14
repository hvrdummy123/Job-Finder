#!/usr/bin/env python3
"""Redetect + refresh companies that currently have zero open jobs."""

from __future__ import annotations

import argparse
import asyncio
import logging

from app import db
from app.config import get_settings
from app.detect import detect_ats
from app.http_client import HttpClient
from app.ingest.pipeline import refresh_source

log = logging.getLogger(__name__)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--names", nargs="*", default=None, help="Optional company name filters")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    await db.init_pool(settings)
    client = HttpClient(settings)
    await client.start()

    if args.names:
        rows = await db.fetch(
            """
            SELECT c.id, c.name, c.careers_url
            FROM companies c
            WHERE c.careers_url NOT LIKE 'portal://%'
              AND lower(c.name) = ANY($1::text[])
            ORDER BY c.name
            """,
            [n.lower() for n in args.names],
        )
    else:
        rows = await db.fetch(
            """
            SELECT c.id, c.name, c.careers_url
            FROM companies c
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::int AS open_jobs
                FROM jobs j
                WHERE j.company_id = c.id AND j.status = 'open'
            ) j ON true
            WHERE c.careers_url NOT LIKE 'portal://%'
              AND COALESCE(j.open_jobs, 0) = 0
            ORDER BY c.name
            LIMIT $1
            """,
            args.limit,
        )

    sem = asyncio.Semaphore(args.concurrency)
    healed = 0
    still_empty = 0
    changed = 0

    async def _one(row) -> None:
        nonlocal healed, still_empty, changed
        async with sem:
            try:
                detected = await detect_ats(row["careers_url"], client, row["name"])
                await db.execute(
                    """
                    UPDATE sources SET
                        ats_type = $2,
                        ats_slug = $3,
                        ats_override = false,
                        detect_confidence = $4,
                        fallback_stage = $5,
                        needs_playwright = $6,
                        failure_count = 0,
                        last_error = NULL,
                        circuit_open_until = NULL,
                        baseline_completed = false,
                        baseline_completed_at = NULL,
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
                changed += 1
                log.info(
                    "redetect %s -> %s slug=%s",
                    row["name"],
                    detected.ats_type,
                    detected.ats_slug,
                )
                source_id = await db.fetchval(
                    "SELECT id FROM sources WHERE company_id=$1", row["id"]
                )
                if source_id:
                    await refresh_source(source_id, client, settings=settings)
            except Exception as exc:
                log.warning("heal failed %s: %s", row["name"], exc)
                return

            open_jobs = await db.fetchval(
                "SELECT COUNT(*) FROM jobs WHERE company_id=$1 AND status='open'",
                row["id"],
            )
            if open_jobs:
                healed += 1
                log.info("healed %s open=%s", row["name"], open_jobs)
            else:
                still_empty += 1
                err = await db.fetchval(
                    "SELECT last_error FROM sources WHERE company_id=$1", row["id"]
                )
                log.info("still empty %s err=%s", row["name"], err)

    try:
        # Prioritize the user's examples first when doing a full pass
        priority = {"canva", "figma", "american express", "anthropic", "affirm", "airtable"}
        ordered = sorted(
            rows,
            key=lambda r: (0 if r["name"].lower() in priority else 1, r["name"].lower()),
        )
        await asyncio.gather(*[_one(r) for r in ordered])
    finally:
        try:
            from app.connectors.playwright_pool import shutdown as shutdown_browser

            await shutdown_browser()
        except Exception:
            pass
        await client.aclose()
        await db.close_pool()

    print(
        f"scanned={len(rows)} redetected={changed} healed={healed} still_empty={still_empty}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
