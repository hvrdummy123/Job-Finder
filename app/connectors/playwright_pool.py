from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.connectors.page_scraper import (
    _LOAD_MORE,
    discover_ats_boards,
    listing_follow_urls,
    merge_stubs,
    stubs_from_greenhouse_payload,
    stubs_from_html,
    stubs_from_json_payload,
    stubs_from_rss,
)
from app.models import JobStub

log = logging.getLogger(__name__)

_browser = None
_lock = asyncio.Lock()


async def fetch_rendered_html(url: str, timeout_ms: int = 20000) -> str | None:
    """Lazy-start a single Chromium browser; returns rendered HTML or None."""
    result = await scrape_with_browser(url, timeout_ms=timeout_ms, html_only=True)
    return result  # type: ignore[return-value]


async def scrape_with_browser(
    url: str,
    *,
    timeout_ms: int = 25000,
    html_only: bool = False,
) -> list[JobStub] | str | None:
    """Render careers page, capture JSON XHR, scrape DOM job links."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ImportError("playwright is not installed") from exc

    global _browser
    async with _lock:
        if _browser is None:
            pw = await async_playwright().start()
            _browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            _browser._pw = pw  # type: ignore[attr-defined]

        context = await _browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1400, "height": 900},
        )
        try:
            page_result = await _scrape_one_page(
                context, url, timeout_ms=timeout_ms, html_only=html_only
            )
            if html_only:
                return page_result  # type: ignore[return-value]

            stubs, html, final_url, board_hints, xhr_urls = page_result  # type: ignore[misc]
            if stubs:
                return stubs

            board_urls: list[str] = list(board_hints or [])
            # Follow listing CTAs / RSS once when the landing page is marketing-only
            follows = listing_follow_urls(html or "", final_url or url)
            follow_timeout = min(20000, timeout_ms)
            for follow in follows[:2]:
                if follow.lower().endswith(".xml") or "/feed/" in follow.lower():
                    try:
                        resp = await context.request.get(follow, timeout=follow_timeout)
                        if resp.ok:
                            rss_stubs = stubs_from_rss(await resp.text(), follow)
                            if rss_stubs:
                                log.info("playwright RSS %s → %s jobs", follow, len(rss_stubs))
                                return rss_stubs
                    except Exception as exc:
                        log.debug("rss follow failed %s: %s", follow, exc)
                    continue
                try:
                    more, more_html, _, more_boards, more_xhr = await _scrape_one_page(
                        context, follow, timeout_ms=follow_timeout, html_only=False
                    )
                    board_urls.extend(more_boards or [])
                    xhr_urls.extend(more_xhr or [])
                    if more_html:
                        html = f"{html or ''}\n{more_html}"
                    if more:
                        return more
                except Exception as exc:
                    log.warning("listing follow failed %s: %s", follow, exc)

            # Hydrate Greenhouse boards discovered only via JS/XHR
            board_map = discover_ats_boards(html or "", *xhr_urls, *board_urls)
            hydrated = await _hydrate_ats_boards(context, board_map, timeout_ms=timeout_ms)
            if hydrated:
                return hydrated
            return stubs
        finally:
            await context.close()


async def _scrape_one_page(
    context: Any,
    url: str,
    *,
    timeout_ms: int,
    html_only: bool,
) -> Any:
    page = await context.new_page()
    json_blobs: list[tuple[str, Any]] = []
    xhr_urls: list[str] = []
    board_hint_urls: list[str] = []

    async def _on_response(response: Any) -> None:
        try:
            resp_url = response.url
            xhr_urls.append(resp_url)
            low = resp_url.lower()
            if any(
                tok in low
                for tok in (
                    "greenhouse.io",
                    "lever.co",
                    "ashbyhq.com",
                    "smartrecruiters.com",
                    "myworkdayjobs.com",
                )
            ):
                board_hint_urls.append(resp_url)
            ctype = (response.headers or {}).get("content-type", "")
            if "json" not in ctype and "javascript" not in ctype:
                if not any(
                    k in low
                    for k in (
                        "job",
                        "posting",
                        "career",
                        "opening",
                        "position",
                        "requisition",
                        "greenhouse",
                        "lever",
                        "ashby",
                    )
                ):
                    return
            if response.status >= 400:
                return
            text = await response.text()
            text = (text or "").strip()
            if not text or text[0] not in "{[":
                return
            data = json.loads(text)
            json_blobs.append((resp_url, data))
        except Exception:
            return

    page.on("response", _on_response)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await _wait_for_challenge(page)
        try:
            await page.wait_for_load_state("networkidle", timeout=min(8000, timeout_ms))
        except Exception:
            pass
        await page.wait_for_timeout(1200)
        await _reveal_listings(page)
        await _click_load_more(page)
        html = await page.content()
        final_url = page.url
    finally:
        await page.close()

    if html_only:
        return html

    from_json: list[JobStub] = []
    for api_url, data in json_blobs:
        # Prefer structured Greenhouse payloads when present
        if "greenhouse.io" in (api_url or "").lower() and isinstance(data, dict) and "jobs" in data:
            boards = discover_ats_boards(api_url)
            slug = next(iter(boards["greenhouse"]), "board")
            extracted = stubs_from_greenhouse_payload(data, slug)
        else:
            extracted = stubs_from_json_payload(data, api_url or url)
        if extracted:
            from_json.extend(extracted)

    from_dom = stubs_from_html(html, final_url or url)
    board_map = discover_ats_boards(html, *xhr_urls, *board_hint_urls)
    hydrated = await _hydrate_ats_boards(context, board_map, timeout_ms=timeout_ms)
    merged = merge_stubs(hydrated, from_json, from_dom)
    log.info(
        "playwright scrape %s → json=%s dom=%s boards=%s merged=%s xhr=%s",
        url,
        len(from_json),
        len(from_dom),
        len(hydrated),
        len(merged),
        len(json_blobs),
    )
    return merged, html, final_url, board_hint_urls, xhr_urls


async def _hydrate_ats_boards(
    context: Any,
    board_map: dict[str, set[str]],
    *,
    timeout_ms: int,
) -> list[JobStub]:
    stubs: list[JobStub] = []
    req_timeout = min(12000, timeout_ms)
    for slug in sorted(board_map.get("greenhouse") or [])[:4]:
        for host in (
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            f"https://boards-api.eu.greenhouse.io/v1/boards/{slug}/jobs",
        ):
            try:
                resp = await context.request.get(host, timeout=req_timeout)
                if not resp.ok:
                    continue
                data = await resp.json()
                got = stubs_from_greenhouse_payload(data, slug)
                if got:
                    log.info("hydrated greenhouse board %s → %s jobs", slug, len(got))
                    stubs.extend(got)
                    break
            except Exception as exc:
                log.debug("greenhouse hydrate %s failed: %s", slug, exc)
    for slug in sorted(board_map.get("lever") or [])[:3]:
        try:
            resp = await context.request.get(
                f"https://api.lever.co/v0/postings/{slug}?mode=json",
                timeout=req_timeout,
            )
            if resp.ok:
                data = await resp.json()
                got = stubs_from_json_payload(
                    data if isinstance(data, list) else {"jobs": data},
                    f"https://jobs.lever.co/{slug}",
                )
                if got:
                    log.info("hydrated lever board %s → %s jobs", slug, len(got))
                    stubs.extend(got)
        except Exception as exc:
            log.debug("lever hydrate %s failed: %s", slug, exc)
    for slug in sorted(board_map.get("ashby") or [])[:3]:
        try:
            resp = await context.request.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                timeout=req_timeout,
            )
            if resp.ok:
                data = await resp.json()
                got = stubs_from_json_payload(data, f"https://jobs.ashbyhq.com/{slug}")
                if got:
                    log.info("hydrated ashby board %s → %s jobs", slug, len(got))
                    stubs.extend(got)
        except Exception as exc:
            log.debug("ashby hydrate %s failed: %s", slug, exc)
    return stubs


async def _wait_for_challenge(page: Any) -> None:
    """Pass lightweight JS bot checks (e.g. Automattic hashcash interstitial)."""
    try:
        title = await page.title()
    except Exception:
        return
    if not title or "checking your browser" not in title.lower():
        return
    try:
        await page.wait_for_function(
            "() => !/checking your browser/i.test(document.title || '')",
            timeout=20000,
        )
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        await page.wait_for_timeout(1000)
    except Exception:
        return


async def _reveal_listings(page: Any) -> None:
    """Scroll / jump to common open-roles anchors so SPA boards mount."""
    for sel in (
        "#greenhouse-open-roles",
        "#open-roles",
        "#open-positions",
        "#open-positons",  # BambooHR typo in the wild
        "#jobs",
        "[id*='open-role']",
        "[data-section='jobs']",
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.scroll_into_view_if_needed(timeout=2000)
                await page.wait_for_timeout(800)
        except Exception:
            continue
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(800)
    except Exception:
        pass


async def _click_load_more(page: Any) -> None:
    """Click a couple of obvious pagination / expand controls."""
    for _ in range(3):
        clicked = False
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=_LOAD_MORE)
                count = await loc.count()
                for i in range(min(count, 2)):
                    btn = loc.nth(i)
                    if await btn.is_visible():
                        await btn.click(timeout=2000)
                        clicked = True
                        await page.wait_for_timeout(1200)
            except Exception:
                continue
        if not clicked:
            for sel in (
                "button:has-text('Load more')",
                "button:has-text('Show more')",
                "a:has-text('View all')",
                "a:has-text('See all jobs')",
                "a:has-text('See our open roles')",
                "a:has-text('View open roles')",
                "a:has-text('See Open Positions')",
            ):
                try:
                    el = page.locator(sel).first
                    if await el.count() and await el.is_visible():
                        await el.click(timeout=2000)
                        clicked = True
                        await page.wait_for_timeout(1200)
                        break
                except Exception:
                    continue
        if not clicked:
            break


async def shutdown() -> None:
    global _browser
    async with _lock:
        if _browser is not None:
            pw = getattr(_browser, "_pw", None)
            await _browser.close()
            if pw is not None:
                await pw.stop()
            _browser = None
