#!/usr/bin/env python3
"""Write data/companies.csv from curated lists."""

from __future__ import annotations

import csv
from pathlib import Path

from scripts.data.company_lists import all_rows, category_counts

OUT = Path(__file__).resolve().parents[1] / "data" / "companies.csv"


def main() -> None:
    rows = all_rows()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "name",
                "careers_url",
                "website",
                "categories",
                "ats_type",
                "ats_slug",
                "notes",
            ],
        )
        writer.writeheader()
        for name, careers_url, website, categories, ats_type, ats_slug in rows:
            writer.writerow(
                {
                    "name": name,
                    "careers_url": careers_url,
                    "website": website,
                    "categories": categories,
                    "ats_type": ats_type,
                    "ats_slug": ats_slug,
                    "notes": categories,
                }
            )
    counts = category_counts(rows)
    print(f"Wrote {len(rows)} companies → {OUT}")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
