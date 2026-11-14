from __future__ import annotations

import hashlib
import re
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urlparse

from selectolax.lexbor import LexborHTMLParser as HTMLParser


def join_locations(*parts: Any) -> str | None:
    """Join unique location strings with '; ' for multi-city postings."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                _add(item)
            return
        if isinstance(value, dict):
            nested = (
                value.get("name")
                or value.get("location")
                or value.get("city")
                or value.get("normalizedLocation")
                or value.get("normalized_location")
            )
            if nested:
                _add(nested)
            return
        text = str(value).strip()
        if not text or re.fullmatch(r"\d+\s+Locations?", text, re.I):
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(text)

    for part in parts:
        _add(part)
    return "; ".join(out) if out else None


def fingerprint_ids(ids: list[str]) -> str:
    joined = "\n".join(sorted(ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def content_hash(text: str | None) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    tree = HTMLParser(html)
    for node in tree.css("script, style"):
        node.decompose()
    text = tree.body.text(separator=" ") if tree.body else tree.text(separator=" ")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Greenhouse uses ms timestamps
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts).astimezone()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
        # Amazon.jobs style: "October  3, 2026"
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y"):
            try:
                return datetime.strptime(re.sub(r"\s+", " ", raw), fmt).astimezone()
            except ValueError:
                continue
        return None
    return None


def host_matches(url: str, *needles: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(n in host for n in needles)


def extract_path_slug(url: str, index: int = -1) -> str | None:
    path = urlparse(url).path.strip("/")
    if not path:
        return None
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    try:
        return parts[index]
    except IndexError:
        return None
