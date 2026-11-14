from __future__ import annotations

import logging
from typing import Any

from app import db
from app.realtime import bus

log = logging.getLogger(__name__)


def job_matches_search(job: dict[str, Any], search: dict[str, Any]) -> bool:
    blob = " ".join(
        filter(
            None,
            [
                job.get("title"),
                job.get("description_text"),
                job.get("department"),
                job.get("location_raw"),
                job.get("city_normalized"),
                job.get("country"),
            ],
        )
    ).lower()

    for term in search.get("include_terms") or []:
        if term and term.lower() not in blob:
            return False
    for term in search.get("exclude_terms") or []:
        if term and term.lower() in blob:
            return False

    locations = search.get("locations") or []
    if locations:
        loc_blob = " ".join(
            filter(None, [job.get("city_normalized"), job.get("country"), job.get("location_raw"), job.get("city")])
        ).lower()
        if not any(loc.lower() in loc_blob for loc in locations):
            return False

    workplace_types = search.get("workplace_types") or []
    if workplace_types and job.get("workplace_type") not in workplace_types:
        return False

    yoe_min = search.get("yoe_min")
    yoe_max = search.get("yoe_max")
    include_unspecified = search.get("include_unspecified_yoe", True)
    job_ymin = job.get("yoe_min")
    job_ymax = job.get("yoe_max")

    if yoe_min is not None or yoe_max is not None:
        if job_ymin is None and job_ymax is None:
            if not include_unspecified:
                return False
        else:
            # Overlap check between job range and search range
            jmin = job_ymin if job_ymin is not None else 0
            jmax = job_ymax if job_ymax is not None else 100
            smin = yoe_min if yoe_min is not None else 0
            smax = yoe_max if yoe_max is not None else 100
            if jmax < smin or jmin > smax:
                return False

    return True


async def match_jobs_to_searches(job_ids: list[int]) -> None:
    if not job_ids:
        return
    searches = await db.fetch("SELECT * FROM saved_searches WHERE is_active = true")
    if not searches:
        return
    jobs = await db.fetch(
        "SELECT id, title, description_text, department, location_raw, city, city_normalized, "
        "country, workplace_type, yoe_min, yoe_max FROM jobs WHERE id = ANY($1::bigint[])",
        job_ids,
    )
    rows: list[tuple[int, int]] = []
    for job in jobs:
        for search in searches:
            if job_matches_search(dict(job), dict(search)):
                rows.append((job["id"], search["id"]))
    if rows:
        await db.executemany(
            """
            INSERT INTO job_search_matches (job_id, saved_search_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )


async def backfill_saved_search(saved_search_id: int) -> int:
    search = await db.fetchrow("SELECT * FROM saved_searches WHERE id = $1", saved_search_id)
    if not search:
        return 0

    await db.execute("DELETE FROM job_search_matches WHERE saved_search_id = $1", saved_search_id)

    matched = 0
    offset = 0
    batch_size = 500
    while True:
        jobs = await db.fetch(
            """
            SELECT id, title, description_text, department, location_raw, city, city_normalized,
                   country, workplace_type, yoe_min, yoe_max
            FROM jobs
            WHERE status = 'open'
            ORDER BY id
            LIMIT $1 OFFSET $2
            """,
            batch_size,
            offset,
        )
        if not jobs:
            break
        rows = [
            (job["id"], saved_search_id)
            for job in jobs
            if job_matches_search(dict(job), dict(search))
        ]
        if rows:
            await db.executemany(
                """
                INSERT INTO job_search_matches (job_id, saved_search_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                rows,
            )
            matched += len(rows)
        offset += len(jobs)
        if len(jobs) < batch_size:
            break

    await bus.publish(
        "saved_search_reindexed",
        {"saved_search_id": saved_search_id, "matched": matched},
    )
    log.info("Backfilled saved search %s with %s matches", saved_search_id, matched)
    return matched
