#!/usr/bin/env python3
"""Force re-baseline for zero-job companies that already have a real ATS."""

from __future__ import annotations

import asyncio
import logging

from app import db
from app.config import get_settings
from app.detect import detect_ats
from app.http_client import HttpClient
from app.ingest.pipeline import refresh_source

log = logging.getLogger(__name__)


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    await db.init_pool(settings)
    client = HttpClient(settings)
    await client.start()

    rows = await db.fetch(
        """
        SELECT c.id, c.name, c.careers_url, s.id AS source_id
        FROM companies c
        JOIN sources s ON s.company_id = c.id
        WHERE c.careers_url NOT LIKE 'portal://%'
          AND s.ats_type IN (
            'greenhouse','lever','ashby','workday','smartrecruiters','oracle','amazon'
          )
          AND NOT EXISTS (
            SELECT 1 FROM jobs j
            WHERE j.company_id = c.id AND j.status = 'open' AND j.is_tech = true
          )
        ORDER BY c.name
        """
    )
    sem = asyncio.Semaphore(5)
    healed = 0
    empty = 0

    async def _one(row) -> None:
        nonlocal healed, empty
        async with sem:
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
                await refresh_source(row["source_id"], client, settings=settings)
            except Exception as exc:
                log.warning("fail %s: %s", row["name"], exc)
                empty += 1
                return
            n = await db.fetchval(
                """
                SELECT COUNT(*) FROM jobs
                WHERE company_id=$1 AND status='open' AND is_tech=true
                """,
                row["id"],
            )
            if n:
                healed += 1
                log.info("healed %s open=%s", row["name"], n)
            else:
                empty += 1
                log.info("still empty %s", row["name"])

    try:
        await asyncio.gather(*[_one(r) for r in rows])
    finally:
        await client.aclose()
        await db.close_pool()

    print(f"scanned={len(rows)} healed={healed} still_empty={empty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
