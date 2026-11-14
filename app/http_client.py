from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.config import Settings
from app.models import FetchResult

log = logging.getLogger(__name__)


class HttpClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._global_sem = asyncio.Semaphore(settings.max_concurrency)
        self._host_sems: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(settings.host_concurrency)
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=httpx.Timeout(self.settings.request_timeout_seconds),
            headers={"User-Agent": self.settings.http_user_agent},
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=40),
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HTTP client not started")
        return self._client

    def _host(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def request(
        self,
        method: str,
        url: str,
        *,
        respect_robots: bool = False,
        headers: dict[str, str] | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
        retries: int = 3,
    ) -> httpx.Response:
        if respect_robots and not await self.allows(url):
            raise PermissionError(f"robots.txt disallows fetch of {url}")

        host = self._host(url)
        host_sem = self._host_sems[host]
        last_exc: Exception | None = None

        for attempt in range(retries + 1):
            async with self._global_sem, host_sem:
                try:
                    response = await self.client.request(
                        method,
                        url,
                        headers=headers,
                        json=json,
                        params=params,
                    )
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                        await _backoff(attempt)
                        continue
                    return response
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_exc = exc
                    if attempt >= retries:
                        break
                    await _backoff(attempt)
        assert last_exc is not None
        raise last_exc

    async def get_text(
        self,
        url: str,
        *,
        respect_robots: bool = False,
        headers: dict[str, str] | None = None,
    ) -> FetchResult:
        response = await self.request("GET", url, respect_robots=respect_robots, headers=headers)
        return FetchResult(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            headers={k.lower(): v for k, v in response.headers.items()},
            text=response.text,
            content=response.content,
        )

    async def allows(self, url: str) -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots:
            rp = RobotFileParser()
            robots_url = f"{base}/robots.txt"
            try:
                resp = await self.client.get(robots_url)
                if resp.status_code >= 400:
                    self._robots[base] = None
                else:
                    rp.parse(resp.text.splitlines())
                    self._robots[base] = rp
            except Exception:
                self._robots[base] = None
        rp = self._robots[base]
        if rp is None:
            return True
        return bool(rp.can_fetch(self.settings.http_user_agent, url))


async def _backoff(attempt: int) -> None:
    delay = (2**attempt) * 0.25 + random.uniform(0, 0.25)
    await asyncio.sleep(delay)
