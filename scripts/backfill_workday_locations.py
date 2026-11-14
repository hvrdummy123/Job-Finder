#!/usr/bin/env python3
"""Re-fetch Workday job details to fill additionalLocations (multi-city postings)."""

from __future__ import annotations

import asyncio
import logging

from app import db
from app.config import get_settings
from app.connectors.workday import WorkdayConnector
from app.http_client import HttpClient
from app.ingest.locations import locations_payload, primary_location
from app.ingest.scrub import scrub_obj, scrub_text
from app.models import Company, JobStub, Source

log = logging.getLogger(__name__)


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    await db.init_pool(settings)
    client = HttpClient(settings)
    await client.start()
    connector = WorkdayConnector()
    sem = asyncio.Semaphore(settings.detail_concurrency)

    rows = await db.fetch(
        """
        SELECT j.id, j.external_job_id, j.title, j.location_raw, j.url,
               s.id AS source_id, s.ats_slug, s.company_id,
               c.name AS company_name, c.careers_url, c.website, c.notes
        FROM jobs j
        JOIN sources s ON s.id = j.source_id
        JOIN companies c ON c.id = j.company_id
        WHERE s.ats_type = 'workday'
          AND j.status = 'open'
          AND j.external_job_id LIKE '/%'
        ORDER BY c.name, j.id
        """
    )

    updated = 0
    failed = 0
    lock = asyncio.Lock()

    async def _one(row) -> None:
        nonlocal updated, failed
        company = Company(
            id=row["company_id"],
            name=row["company_name"],
            careers_url=row["careers_url"],
            website=row["website"],
            notes=row["notes"],
        )
        source = Source(
            id=row["source_id"],
            company_id=row["company_id"],
            ats_type="workday",
            ats_slug=row["ats_slug"],
        )
        stub = JobStub(
            external_job_id=row["external_job_id"],
            title=row["title"],
            url=row["url"],
            location_raw=row["location_raw"],
            raw={},
        )
        async with sem:
            try:
                draft = await connector.get_job(company, source, stub, client)
            except Exception as exc:
                log.warning("detail failed job=%s: %s", row["id"], exc)
                async with lock:
                    failed += 1
                return

        loc_raw = draft.location_raw
        if not loc_raw:
            return
        old = row["location_raw"] or ""
        if loc_raw == old:
            return

        loc = primary_location(loc_raw)
        locs = scrub_obj(locations_payload(loc_raw))
        await db.execute(
            """
            UPDATE jobs SET
                location_raw = $2,
                city = $3,
                state = $4,
                country = $5,
                city_normalized = $6,
                locations = $7::jsonb,
                updated_at = now()
            WHERE id = $1
            """,
            row["id"],
            scrub_text(loc_raw),
            scrub_text(loc.city),
            scrub_text(loc.state),
            scrub_text(loc.country),
            scrub_text(loc.city_normalized or loc.city),
            locs,
        )
        async with lock:
            updated += 1
            if updated % 25 == 0:
                log.info("updated %s …", updated)

    try:
        await asyncio.gather(*[_one(r) for r in rows])
    finally:
        await client.aclose()
        await db.close_pool()

    print(f"updated={updated} failed={failed} scanned={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
