from __future__ import annotations

import re
from dataclasses import dataclass

_YOE_PATTERNS = [
    re.compile(
        r"(?P<min>\d+)\s*[\+\-–—to]+\s*(?P<max>\d+)\s*\+?\s*(?:years|yrs|yr)\b",
        re.I,
    ),
    re.compile(
        r"(?P<min>\d+)\s*\+\s*(?:years|yrs|yr)\b",
        re.I,
    ),
    re.compile(
        r"(?:at least|minimum of|min(?:imum)?\.?)\s*(?P<min>\d+)\s*(?:years|yrs|yr)\b",
        re.I,
    ),
    re.compile(
        r"(?P<min>\d+)\s*(?:years|yrs|yr)\s+(?:of\s+)?(?:experience|exp)\b",
        re.I,
    ),
]

_REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|distributed)\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_ONSITE_RE = re.compile(r"\b(on[-\s]?site|in[-\s]?office)\b", re.I)

_SENIORITY_MAP = [
    (re.compile(r"\bintern\b", re.I), "intern"),
    (re.compile(r"\b(new grad|university grad|entry[\s-]?level|junior)\b", re.I), "junior"),
    (re.compile(r"\b(mid[\s-]?level|intermediate)\b", re.I), "mid"),
    (re.compile(r"\b(senior|sr\.?)\b", re.I), "senior"),
    (re.compile(r"\b(staff|principal|distinguished)\b", re.I), "staff"),
    (re.compile(r"\b(manager|director|head of|vp|vice president)\b", re.I), "manager"),
]

_SKILL_HINTS = [
    "python", "typescript", "javascript", "react", "node", "java", "kotlin", "go", "golang",
    "rust", "c++", "c#", ".net", "sql", "postgres", "mysql", "mongodb", "redis", "kafka",
    "aws", "gcp", "azure", "kubernetes", "docker", "terraform", "spark", "pytorch",
    "tensorflow", "fastapi", "django", "flask", "spring", "graphql", "next.js",
]


@dataclass(slots=True)
class ParsedJD:
    yoe_min: int | None = None
    yoe_max: int | None = None
    workplace_type: str = "unknown"
    seniority: str | None = None
    skills: list[str] | None = None


def parse_jd(title: str, description_text: str | None, location_raw: str | None = None) -> ParsedJD:
    blob = f"{title}\n{description_text or ''}\n{location_raw or ''}"
    yoe_min, yoe_max = _parse_yoe(blob)
    workplace = _parse_workplace(blob)
    seniority = _parse_seniority(title) or _parse_seniority(blob)
    skills = _parse_skills(blob)
    return ParsedJD(
        yoe_min=yoe_min,
        yoe_max=yoe_max,
        workplace_type=workplace,
        seniority=seniority,
        skills=skills,
    )


def _parse_yoe(text: str) -> tuple[int | None, int | None]:
    for pattern in _YOE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        groups = m.groupdict()
        mn = int(groups["min"]) if groups.get("min") else None
        mx = int(groups["max"]) if groups.get("max") else None
        if mn is not None and mx is None and "+" in m.group(0):
            return mn, None
        if mn is not None and mx is not None and mn > mx:
            mn, mx = mx, mn
        if mn is not None and mn > 40:
            continue
        return mn, mx
    return None, None


def _parse_workplace(text: str) -> str:
    if _HYBRID_RE.search(text):
        return "hybrid"
    if _REMOTE_RE.search(text):
        return "remote"
    if _ONSITE_RE.search(text):
        return "onsite"
    return "unknown"


def _parse_seniority(text: str) -> str | None:
    for pattern, label in _SENIORITY_MAP:
        if pattern.search(text):
            return label
    return None


def _parse_skills(text: str) -> list[str]:
    lower = text.lower()
    found = [s for s in _SKILL_HINTS if s in lower]
    return found[:40]
