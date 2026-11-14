#!/usr/bin/env python3
"""Recompute multi-city locations for all ATS types from stored job.raw JSON."""

from __future__ import annotations

import asyncio
import logging

from app import db
from app.config import get_settings
from app.ingest.ats_locations import location_raw_from_raw
from app.ingest.locations import load_alias_cache, locations_payload, primary_location
from app.ingest.scrub import scrub_obj, scrub_text

log = logging.getLogger(__name__)


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    await db.init_pool(settings)
    await load_alias_cache()

    rows = await db.fetch(
        """
        SELECT j.id, j.location_raw, j.locations, j.raw, s.ats_type
        FROM jobs j
        JOIN sources s ON s.id = j.source_id
        WHERE j.status = 'open'
          AND j.raw IS NOT NULL
          AND j.raw::text NOT IN ('{}', 'null')
        ORDER BY s.ats_type, j.id
        """
    )

    updated = 0
    by_ats: dict[str, int] = {}
    try:
        for row in rows:
            ats = row["ats_type"]
            new_raw = location_raw_from_raw(ats, row["raw"], row["location_raw"])
            if not new_raw:
                continue
            old = row["location_raw"] or ""
            old_locs = row["locations"] or []
            if isinstance(old_locs, str):
                old_locs = []
            new_locs = scrub_obj(locations_payload(new_raw))
            richer = (
                new_raw != old
                or (";" in new_raw and len(new_locs) > len(old_locs or []))
                or (len(new_locs) > 1 and len(old_locs or []) <= 1)
            )
            if not richer and new_raw == old and len(new_locs) == len(old_locs or []):
                # Still rewrite if normalization (Bangalore→Bengaluru) improved
                if new_locs == (old_locs or []):
                    continue

            loc = primary_location(new_raw)
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
                scrub_text(new_raw),
                scrub_text(loc.city),
                scrub_text(loc.state),
                scrub_text(loc.country),
                scrub_text(loc.city_normalized or loc.city),
                new_locs,
            )
            updated += 1
            by_ats[ats] = by_ats.get(ats, 0) + 1
            if updated % 200 == 0:
                log.info("updated %s …", updated)
    finally:
        await db.close_pool()

    print(f"updated={updated} scanned={len(rows)} by_ats={by_ats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
