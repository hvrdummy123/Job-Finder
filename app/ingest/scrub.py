"""Strip characters Postgres rejects (NUL bytes in UTF-8 text/jsonb)."""

from __future__ import annotations

from typing import Any


def scrub_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\x00", "")


def scrub_obj(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: scrub_obj(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_obj(v) for v in value]
    return value
