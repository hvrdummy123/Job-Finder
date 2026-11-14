"""Keep tech / AI / quant / fintech / blockchain roles — title-based only.

Intentionally broad: ingest nearly all technical titles; users narrow via
the Active Titles filter on the Titles page.
"""

from __future__ import annotations

import re

# Match TITLE only (never description body).
_INCLUDE = re.compile(
    r"""
    \b(
        # Software / development
        software|
        sde\b|swe\b|mts\b|member\s*of\s*technical\s*staff|
        application\s*s?|
        full[\s-]?stack|fullstack|front[\s-]?end|frontend|back[\s-]?end|backend|

        # Engineering catch-alls + common disciplines
        engineer|developer|programmer|architect|scientist|
        devops|sre\b|mlops|aiops|platform|infrastructure|reliability|
        systems?|firmware|embedded|hardware|networking|network|
        kubernetes|distributed|compiler|graphics|game|

        # AI / ML / agents
        machine\s*learning|\bml\b|deep\s*learning|
        artificial\s*intelligence|\bai\b|
        llm|gpt|generative|agentic|\bagents?\b|prompt|
        applied\s*scientist|research\s*(engineer|scientist)|
        computer\s*vision|nlp\b|speech\s*recognition|

        # Data
        data\s*(engineer|scientist|architect|analyst)|
        analytics?|etl\b|\bbi\b|business\s*intelligence|
        database|warehouse|spark|hadoop|

        # Quant / fintech
        quant\b|quantitative|
        payments?|trading|fintech|banking|crypto|defi\b|
        risk\s*(engineer|developer|quant|analyst)|
        credit\s*(engineer|risk|analyst)|

        # Blockchain / web3
        blockchain|web3|smart\s*contract|solidity|ethereum|protocol|

        # Security
        security|appsec|infosec|cyber|detection\s*engineer|soc\b|

        # Mobile / cloud
        ios\b|android|mobile|flutter|react\s*native|cloud|

        # QA
        \bqa\b|sdet\b|test\s*automation|automation\s*engineer|quality\s*assurance|

        # Analysts (qualified)
        (data|business|systems?|security|financial|risk|credit|product|
         operations?|ops|research|quantitative|quant|crypto|blockchain|
         payments?|trading|fraud|compliance|bi|software|technical|it|
         technology|marketing\s*data)\s*analysts?|

        # Architecture / consulting (tech)
        solutions?\s*architect|enterprise\s*architect|technical\s*architect|
        software\s*architect|cloud\s*architect|data\s*architect|
        technical\s*consultant|technology\s*consultant|presales\s*engineer|
        forward\s*deployed|customer\s*engineer|sales\s*engineer|
        aem\b|workfront|

        # Leadership
        engineering\s*manager|director\s*of\s*engineering|
        vp\s*(of\s*)?engineering|cto\b|head\s*of\s*(engineering|technology|ai|data|security)|
        staff\s*engineer|principal\s*engineer|distinguished\s*engineer|
        technologist|tech\s*lead|technical\s*lead|team\s*lead\s*engineer
    )\b
    """,
    re.I | re.X,
)

_EXCLUDE = re.compile(
    r"""
    \b(
        human\s*resources|\bhr\b(?!\s*tech)|recruiter|recruiting|talent\s*acquisition|people\s*ops|
        business\s*development|\bbd\s*manager\b|
        sales\s*(manager|executive|rep|associate)(?!\s*engineer)|
        account\s*executive|
        account\s*manager(?!\s+(engineer|technical))|
        customer\s*success\s*manager|customer\s*support|support\s*specialist|
        brand\s*manager|content\s*writer|copywriter|social\s*media\s*manager|
        legal\b|counsel|paralegal|attorney|
        office\s*manager|executive\s*assistant|administrative\s*assistant|
        nurse|physician|pharmacist|barista|receptionist|teacher|professor|
        diversity|inclusion|milestones|our\s*stories|life\s*at|
        fraud\s*alert|campus\s*hiring|nqt\s*hiring|mba\s*hiring|
        value\s*proposition|press\s*coverage|
        marketing\s*analyst(?!\s*(engineer|data|tech))|
        hr\s*analyst|people\s*analyst|
        financial\s*advisor|wealth\s*advisor|
        interior\s*design|graphic\s*design(?!\s*engineer)|
        retail\s*associate|store\s*manager
    )\b
    """,
    re.I | re.X,
)

_NOT_A_JOB_TITLE = re.compile(
    r"""
    ^(brazil|india|usa|uk|bahrain|argentina|singapore|canada|germany|france|
      careers?|jobs?|home|about|teams?|locations?|students?|university|
      internship|entry\s*level|experienced|slp|xplore|launchpad)\s*$
    |
    \b(diversity|inclusion|milestones|our\s*stories|empowers|learning\s*culture|
       recruitment\s*fraud|life\s*at|why\s*join|benefits|perks)\b
    """,
    re.I | re.X,
)


def is_tech_role(
    title: str,
    *,
    department: str | None = None,
    description_text: str | None = None,
) -> bool:
    """Broad tech/AI/fintech/blockchain title gate."""
    title = (title or "").strip()
    if not title or len(title) < 3:
        return False
    if _NOT_A_JOB_TITLE.search(title):
        return False
    included = bool(_INCLUDE.search(title))
    if not included:
        return False
    if _EXCLUDE.search(title):
        # Still keep clear engineering/tech titles
        if re.search(
            r"\b(engineer|developer|sde|swe|architect|scientist|programmer|"
            r"quant|technologist|ml\b|machine\s*learning|\bai\b)\b",
            title,
            re.I,
        ):
            return not re.search(
                r"\b(recruiter|talent\s*acquisition|account\s*executive|"
                r"hr\s*business|financial\s*advisor)\b",
                title,
                re.I,
            )
        return False
    return True


def looks_like_job_url(url: str | None) -> bool:
    """Reject careers landing / marketing / blog URLs."""
    if not url:
        return False
    u = url.lower()
    # Explicit job-id query params win even on paths like /about/jobs/apply?gh_jid=
    if any(
        token in u
        for token in ("gh_jid=", "jobid=", "job_id=", "requisitionid=", "reqid=")
    ):
        return True

    bad = (
        "/insights/",
        "/blog",
        "/press",
        "/news/",
        "/about/",
        "/diversity",
        "/inclusion",
        "/life-at",
        "/students",
        "/university",
        "/fraud",
        "/benefits",
        "sitemap",
        "/category/",
        "/categories/",
        "/teams/",
        "/find-your-team",
        "/job-categories",
    )
    if any(b in u for b in bad):
        return False

    good = (
        "/job/",
        "/jobs/",
        "/position/",
        "/positions/",
        "/opening/",
        "/requisition",
        "gh_jid=",
        "jobid=",
        "job_id=",
        "/jobdetail",
        "/job-details",
        "/job_details",
        "/careers/application",
        "/work-with-us/job/",
        "boards.greenhouse.io",
        "boards-api.greenhouse.io",
        "jobs.lever.co",
        "jobs.ashbyhq.com",
        "myworkdayjobs.com",
        "amazon.jobs",
        "remoterocketship.com",
        "wellfound.com",
        "angel.co",
        "naukri.com",
        "indeed.com",
        "smartrecruiters.com",
        "oraclecloud.com",
        "/en/sites/",
        "/sites/cx_",
        "/en/jobs/",
        "viewjob",
        "job-listings",
        "job-listing",
        "postingurl",
        "candidateexperience",
        "job-boards.greenhouse.io",
        "job-boards.eu.greenhouse.io",
        "/job-board/",
    )

    if any(g in u for g in good):
        path = u.split("?", 1)[0].rstrip("/")
        last = path.rsplit("/", 1)[-1]
        # "/job?gh_jid=…" already handled above; bare "/jobs" landing pages stay out
        if last in {
            "jobs",
            "job",
            "careers",
            "career",
            "openings",
            "positions",
            "search",
            "engineering",
            "software-engineering",
            "technology",
        }:
            return False
        return True

    return False
