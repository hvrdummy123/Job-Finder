from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app import db
from app.ingest.locations import expand_location_terms

router = APIRouter(prefix="/jobs", tags=["jobs"])

SortOption = Literal["newest", "posted", "company", "relevance", "experience"]


class JobStatePatch(BaseModel):
    starred: bool | None = None
    hidden: bool | None = None
    applied: bool | None = None
    notes: str | None = None


@router.get("")
async def list_jobs(
    q: str | None = None,
    company_id: list[int] | None = Query(default=None),
    company: str | None = None,
    status: str = "open",
    workplace_type: list[str] | None = Query(default=None),
    employment_type: list[str] | None = Query(default=None),
    seniority: list[str] | None = Query(default=None),
    department: str | None = None,
    country: str | None = None,
    city: str | None = None,
    location: list[str] | None = Query(default=None),
    yoe_min: int | None = None,
    yoe_max: int | None = None,
    include_unspecified_yoe: bool = True,
    posted_within_hours: int | None = None,
    first_seen_within_hours: int | None = None,
    posted_after: datetime | None = None,
    posted_before: datetime | None = None,
    first_seen_after: datetime | None = None,
    first_seen_before: datetime | None = None,
    saved_search_id: int | None = None,
    starred: bool | None = None,
    hidden: bool = False,
    applied: bool | None = None,
    new_since_visit: bool = False,
    exclude_baseline: bool = False,
    category: str | None = None,
    source_portal: str | None = None,
    tech_only: bool = True,
    active_titles: bool = False,
    sort: SortOption = "posted",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    where: list[str] = ["1=1"]
    args: list[Any] = []

    def add(clause: str, *values: Any) -> None:
        placeholders = []
        for v in values:
            args.append(v)
            placeholders.append(f"${len(args)}")
        where.append(clause.format(*placeholders))

    if status:
        add("j.status = {}", status)
    if company_id:
        add("j.company_id = ANY({}::bigint[])", company_id)
    if company:
        slug = company.strip().lower().strip("/")
        add(
            "trim(both '-' from regexp_replace(lower(c.name), '[^a-z0-9]+', '-', 'g')) = {}",
            slug,
        )
    if workplace_type:
        add("j.workplace_type = ANY({}::text[])", workplace_type)
    if employment_type:
        add("j.employment_type = ANY({}::text[])", employment_type)
    if seniority:
        add("j.seniority = ANY({}::text[])", seniority)
    if department:
        add("j.department ILIKE {}", f"%{department}%")

    # Multi-location filter (preferred). Falls back to legacy city/country.
    location_terms = list(location or [])
    if city:
        location_terms.append(city)
    if country:
        location_terms.append(country)
    if location_terms:
        await _add_location_filters(add, location_terms)

    if yoe_min is not None or yoe_max is not None:
        smin = yoe_min if yoe_min is not None else 0
        smax = yoe_max if yoe_max is not None else 100
        if include_unspecified_yoe:
            add(
                "((j.yoe_min IS NULL AND j.yoe_max IS NULL) OR "
                "(COALESCE(j.yoe_max, 100) >= {} AND COALESCE(j.yoe_min, 0) <= {}))",
                smin,
                smax,
            )
        else:
            add(
                "(j.yoe_min IS NOT NULL OR j.yoe_max IS NOT NULL) AND "
                "COALESCE(j.yoe_max, 100) >= {} AND COALESCE(j.yoe_min, 0) <= {}",
                smin,
                smax,
            )

    if posted_within_hours is not None:
        add(
            "j.posted_at >= now() - make_interval(hours => {}::double precision)",
            float(posted_within_hours),
        )
    if first_seen_within_hours is not None:
        add(
            "j.first_seen_at >= now() - make_interval(hours => {}::double precision)",
            float(first_seen_within_hours),
        )
    if posted_after:
        add("j.posted_at >= {}", posted_after)
    if posted_before:
        add("j.posted_at <= {}", posted_before)
    if first_seen_after:
        add("j.first_seen_at >= {}", first_seen_after)
    if first_seen_before:
        add("j.first_seen_at <= {}", first_seen_before)

    if exclude_baseline or new_since_visit:
        where.append("j.is_baseline = false")
    if new_since_visit:
        where.append("j.first_seen_at > (SELECT last_visit_at FROM user_visits WHERE id = 1)")
    if saved_search_id is not None:
        add(
            "EXISTS (SELECT 1 FROM job_search_matches m WHERE m.job_id = j.id AND m.saved_search_id = {})",
            saved_search_id,
        )

    if hidden is False:
        where.append("COALESCE(u.hidden, false) = false")
    elif hidden is True:
        where.append("COALESCE(u.hidden, false) = true")
    # Favorited / applied: either alone is AND; both selected → OR (my tracked jobs)
    if starred is True and applied is True:
        where.append("(COALESCE(u.starred, false) = true OR COALESCE(u.applied, false) = true)")
    else:
        if starred is not None:
            add("COALESCE(u.starred, false) = {}", starred)
        if applied is not None:
            add("COALESCE(u.applied, false) = {}", applied)
    if category:
        add("{} = ANY(c.categories)", category)
    if source_portal:
        add("j.source_portal = {}", source_portal)
    if tech_only:
        where.append("j.is_tech = true")
    if active_titles:
        # Extra layer: only titles the user marked as "actively looking for"
        where.append(
            """EXISTS (
                SELECT 1
                FROM user_active_role_titles ua
                JOIN role_title_catalog rt ON rt.id = ua.title_id
                WHERE j.title ILIKE '%' || rt.title || '%'
                   OR EXISTS (
                        SELECT 1 FROM unnest(rt.aliases) al
                        WHERE COALESCE(al, '') <> ''
                          AND j.title ILIKE '%' || al || '%'
                   )
            )"""
        )

    rank_select = "0::float AS rank"
    q_param: int | None = None
    if q:
        add("j.search_vector @@ websearch_to_tsquery('english', {})", q)
        q_param = len(args)
        rank_select = f"ts_rank(j.search_vector, websearch_to_tsquery('english', ${q_param})) AS rank"

    if sort == "relevance" and q_param is not None:
        order_by = "rank DESC NULLS LAST, COALESCE(j.posted_at, j.first_seen_at) DESC"
    elif sort == "posted" or sort == "newest":
        # Prefer company posted_at; fall back to first_seen_at only when missing
        order_by = "COALESCE(j.posted_at, j.first_seen_at) DESC, j.id DESC"
    elif sort == "company":
        order_by = "c.name ASC, COALESCE(j.posted_at, j.first_seen_at) DESC"
    elif sort == "experience":
        order_by = "j.yoe_min ASC NULLS LAST, COALESCE(j.posted_at, j.first_seen_at) DESC"
    else:
        order_by = "COALESCE(j.posted_at, j.first_seen_at) DESC, j.id DESC"

    where_sql = " AND ".join(where)
    args.extend([limit, offset])
    limit_ph = f"${len(args) - 1}"
    offset_ph = f"${len(args)}"

    sql = f"""
        SELECT j.id, j.company_id, c.name AS company_name, j.external_job_id, j.title,
               j.url, j.apply_url, j.location_raw, j.city, j.city_normalized, j.state, j.country,
               j.locations, j.workplace_type, j.employment_type, j.department, j.seniority,
               j.yoe_min, j.yoe_max, j.posted_at, j.first_seen_at, j.last_seen_at,
               j.status, j.is_baseline, j.is_tech, j.skills, j.source_portal, j.employer_name,
               c.categories AS company_categories,
               COALESCE(u.starred, false) AS starred,
               COALESCE(u.hidden, false) AS hidden,
               COALESCE(u.applied, false) AS applied,
               (j.first_seen_at > (SELECT last_visit_at FROM user_visits WHERE id = 1)
                    AND j.is_baseline = false) AS is_new_since_visit,
               {rank_select}
        FROM jobs j
        JOIN companies c ON c.id = j.company_id
        LEFT JOIN user_job_state u ON u.job_id = j.id
        WHERE {where_sql}
        ORDER BY {order_by}
        LIMIT {limit_ph} OFFSET {offset_ph}
    """
    rows = await db.fetch(sql, *args)

    count_args = args[:-2]
    total = await db.fetchval(
        f"""
        SELECT COUNT(*)
        FROM jobs j
        JOIN companies c ON c.id = j.company_id
        LEFT JOIN user_job_state u ON u.job_id = j.id
        WHERE {where_sql}
        """,
        *count_args,
    )
    return {"total": total, "limit": limit, "offset": offset, "items": [_serialize(r) for r in rows]}


async def _add_location_filters(add, terms: list[str]) -> None:
    """Match any selected location term (OR). Each term expands via aliases."""
    expansions = await expand_location_terms(terms)
    if not expansions:
        return

    term_clauses: list[str] = []
    term_args: list[Any] = []

    for exp in expansions:
        parts: list[str] = []
        # Capture args relative to this term; we'll flatten via add() using a combined clause
        local: list[Any] = []

        def _ph(value: Any) -> str:
            local.append(value)
            return f"__{len(local)}__"

        for city in exp.cities:
            parts.append(
                f"(j.city ILIKE {_ph('%' + city + '%')} OR j.city_normalized ILIKE {_ph('%' + city + '%')})"
            )
        for state in exp.states:
            parts.append(f"j.state ILIKE {_ph('%' + state + '%')}")
        for country in exp.countries:
            parts.append(f"j.country ILIKE {_ph('%' + country + '%')}")
        for sub in exp.raw_substrings:
            # Short codes (IN, KA, US) must not match as loose substrings
            # ("in" would hit Washington). Match as ISO-prefix or whole token.
            if len(sub) <= 3:
                parts.append(
                    f"(j.location_raw ~* {_ph('(^|[,\\\\s])' + re.escape(sub) + '([,\\\\s]|$)')})"
                )
            else:
                parts.append(f"j.location_raw ILIKE {_ph('%' + sub + '%')}")

        # JSONB multi-location entries — only cities/states/countries + longer raw terms
        safe_needles = _uniq_ci([*exp.cities, *exp.states, *exp.countries])
        long_raw = [s for s in exp.raw_substrings if len(s) > 3]
        safe_needles = _uniq_ci([*safe_needles, *long_raw])
        if safe_needles:
            patterns = [f"%{n}%" for n in safe_needles]
            parts.append(
                f"""EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(COALESCE(j.locations, '[]'::jsonb)) loc
                    WHERE loc->>'city' ILIKE ANY({_ph(patterns)})
                       OR loc->>'city_normalized' ILIKE ANY({_ph(patterns)})
                       OR loc->>'state' ILIKE ANY({_ph(patterns)})
                       OR loc->>'country' ILIKE ANY({_ph(patterns)})
                       OR loc->>'raw' ILIKE ANY({_ph(patterns)})
                )"""
            )

        if not parts:
            continue

        # Rewrite placeholders to real $N via add by building one OR group
        clause = "(" + " OR ".join(parts) + ")"
        # Manually bind: convert __i__ markers
        bound_args: list[Any] = []
        final = clause
        for i, val in enumerate(local, start=1):
            # Will be replaced when we call add with all values — use format style
            final = final.replace(f"__{i}__", "{}")
            bound_args.append(val)
        term_clauses.append(final)
        term_args.extend(bound_args)

    if not term_clauses:
        return
    # Match jobs that satisfy ANY selected location
    add("(" + " OR ".join(term_clauses) + ")", *term_args)


def _uniq_ci(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if not v:
            continue
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


@router.get("/{job_id}")
async def get_job(job_id: int) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        SELECT j.*, c.name AS company_name,
               COALESCE(u.starred, false) AS starred,
               COALESCE(u.hidden, false) AS hidden,
               COALESCE(u.applied, false) AS applied,
               u.notes
        FROM jobs j
        JOIN companies c ON c.id = j.company_id
        LEFT JOIN user_job_state u ON u.job_id = j.id
        WHERE j.id = $1
        """,
        job_id,
    )
    if not row:
        raise HTTPException(404, "Job not found")
    return _serialize(row)


@router.patch("/{job_id}/state")
async def patch_job_state(job_id: int, body: JobStatePatch) -> dict[str, Any]:
    exists = await db.fetchval("SELECT 1 FROM jobs WHERE id = $1", job_id)
    if not exists:
        raise HTTPException(404, "Job not found")

    data = body.model_dump(exclude_unset=True)
    row = await db.fetchrow(
        """
        INSERT INTO user_job_state (job_id, starred, hidden, applied, notes, applied_at, updated_at)
        VALUES (
            $1,
            COALESCE($2, false),
            COALESCE($3, false),
            COALESCE($4, false),
            $5,
            CASE WHEN COALESCE($4, false) THEN now() ELSE NULL END,
            now()
        )
        ON CONFLICT (job_id) DO UPDATE SET
            starred = COALESCE($2, user_job_state.starred),
            hidden = COALESCE($3, user_job_state.hidden),
            applied = COALESCE($4, user_job_state.applied),
            notes = COALESCE($5, user_job_state.notes),
            applied_at = CASE
                WHEN $4 IS NULL THEN user_job_state.applied_at
                WHEN $4 THEN COALESCE(user_job_state.applied_at, now())
                ELSE NULL
            END,
            updated_at = now()
        RETURNING *
        """,
        job_id,
        data.get("starred"),
        data.get("hidden"),
        data.get("applied"),
        data.get("notes"),
    )
    return _serialize(row)


def _serialize(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return data
