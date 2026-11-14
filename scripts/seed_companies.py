#!/usr/bin/env python3
"""Seed companies from a CSV and auto-detect ATS.

CSV columns (header required):
  name,careers_url
Optional: website,notes,categories,ats_type,ats_slug

Usage:
  uv run python -m scripts.generate_companies_csv
  uv run python -m scripts.seed_companies data/companies.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from app import db
from app.config import get_settings
from app.detect import detect_ats
from app.http_client import HttpClient

PORTAL_TYPES = {"indeed", "wellfound", "naukri", "rocketship"}


def _categories(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [c.strip() for c in raw.replace(";", ",").split(",") if c.strip()]


async def seed(path: Path, *, skip_detect: bool = False) -> int:
    settings = get_settings()
    await db.init_pool(settings)
    await db.run_migrations(settings)

    client = HttpClient(settings)
    await client.start()

    manual: list[str] = []
    ok = 0
    updated = 0

    try:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "name" not in reader.fieldnames or "careers_url" not in reader.fieldnames:
                print("CSV must have headers: name,careers_url", file=sys.stderr)
                return 2

            for row in reader:
                name = (row.get("name") or "").strip()
                careers_url = (row.get("careers_url") or "").strip()
                if not name or not careers_url:
                    continue

                cats = _categories(row.get("categories") or row.get("notes"))
                override_type = (row.get("ats_type") or "").strip() or None
                override_slug = (row.get("ats_slug") or "").strip() or None
                is_portal = careers_url.startswith("portal://") or (override_type in PORTAL_TYPES)

                existing = await db.fetchrow(
                    "SELECT id FROM companies WHERE lower(name) = lower($1)",
                    name,
                )
                if existing:
                    await db.execute(
                        """
                        UPDATE companies SET
                            careers_url = $2,
                            website = COALESCE($3, website),
                            notes = COALESCE($4, notes),
                            categories = CASE WHEN $5::text[] <> '{}' THEN $5 ELSE categories END,
                            updated_at = now()
                        WHERE id = $1
                        """,
                        existing["id"],
                        careers_url,
                        (row.get("website") or None),
                        (row.get("notes") or None),
                        cats,
                    )
                    if override_type:
                        await db.execute(
                            """
                            UPDATE sources SET
                                ats_type = $2,
                                ats_slug = COALESCE($3, ats_slug),
                                ats_override = true,
                                fallback_stage = CASE WHEN $2 = ANY($4::text[]) THEN 'api' ELSE fallback_stage END,
                                updated_at = now()
                            WHERE company_id = $1
                            """,
                            existing["id"],
                            override_type,
                            override_slug,
                            list(PORTAL_TYPES),
                        )
                    print(f"update existing: {name}")
                    updated += 1
                    continue

                if override_type or is_portal or skip_detect:
                    ats_type = override_type or ("generic" if not is_portal else "unknown")
                    if is_portal and not override_type:
                        # portal://indeed/... → indeed
                        ats_type = careers_url.split("://", 1)[1].split("/", 1)[0].replace("indeed-in", "indeed")
                    ats_slug = override_slug
                    confidence, fallback, needs_pw = 1.0, "api", False
                    # Only mark override for explicit CSV ats_type or portal feeds —
                    # --skip-detect alone must not block later redetect.
                    overridden = bool(override_type) or is_portal
                else:
                    detected = await detect_ats(careers_url, client, name)
                    ats_type = detected.ats_type
                    ats_slug = detected.ats_slug
                    confidence = detected.confidence
                    fallback = detected.fallback_stage
                    needs_pw = detected.needs_playwright
                    overridden = False

                company = await db.fetchrow(
                    """
                    INSERT INTO companies (name, careers_url, website, notes, categories)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id
                    """,
                    name,
                    careers_url,
                    (row.get("website") or None),
                    (row.get("notes") or None),
                    cats,
                )
                await db.execute(
                    """
                    INSERT INTO sources (
                        company_id, ats_type, ats_slug, ats_override,
                        detect_confidence, fallback_stage, needs_playwright
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                    """,
                    company["id"],
                    ats_type,
                    ats_slug,
                    overridden,
                    confidence,
                    fallback,
                    needs_pw,
                )

                status = f"{name}: {ats_type}" + (f"/{ats_slug}" if ats_slug else "")
                if ats_type in {"unknown", "generic"}:
                    manual.append(f"{name} — {ats_type}")
                    status += " [MANUAL?]"
                elif ats_type in {"greenhouse", "lever", "ashby", "workday"} and not ats_slug:
                    manual.append(f"{name} ({ats_type}) — missing slug")
                    status += " [NEEDS SLUG]"
                print(status)
                ok += 1
    finally:
        await client.aclose()
        await db.close_pool()

    print()
    print(f"Seeded {ok} new · updated {updated}.")
    if manual:
        print("Needs manual attention:")
        for line in manual:
            print(f"  - {line}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed companies from CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument(
        "--skip-detect",
        action="store_true",
        help="Do not fetch careers pages during seed (use CSV ats hints only)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(seed(args.csv_path, skip_detect=args.skip_detect)))


if __name__ == "__main__":
    main()
