from __future__ import annotations

import asyncio
import logging

from app import db
from app.config import Settings
from app.http_client import HttpClient
from app.ingest.pipeline import RefreshOutcome, refresh_source
from app.realtime import bus

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, settings: Settings, client: HttpClient) -> None:
        self.settings = settings
        self.client = client
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # Full refresh can be interrupted by a single-company priority refresh
        self._full_refresh_task: asyncio.Task | None = None
        self._full_refresh_cancel = asyncio.Event()
        self._priority_lock = asyncio.Lock()

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        self._stop.set()
        self._full_refresh_cancel.set()
        if self._full_refresh_task and not self._full_refresh_task.done():
            self._full_refresh_task.cancel()
        if self._task:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        log.info("Scheduler started")
        while not self._stop.is_set():
            try:
                # Pause background ticks while a full manual refresh is running
                if self._full_refresh_task and not self._full_refresh_task.done():
                    pass
                else:
                    await self._tick()
            except Exception:
                log.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.scheduler_tick_seconds
                )
            except TimeoutError:
                continue
        log.info("Scheduler stopped")

    async def _tick(self) -> None:
        rows = await db.fetch(
            """
            UPDATE sources s
            SET updated_at = now()
            FROM (
                SELECT id FROM sources
                WHERE next_poll_at <= now()
                  AND (circuit_open_until IS NULL OR circuit_open_until <= now())
                ORDER BY next_poll_at
                FOR UPDATE SKIP LOCKED
                LIMIT $1
            ) due
            WHERE s.id = due.id
            RETURNING s.id
            """,
            self.settings.scheduler_claim_limit,
        )
        if not rows:
            return

        run_id = await db.fetchval(
            """
            INSERT INTO crawl_runs (trigger, companies_total)
            VALUES ('scheduler', $1)
            RETURNING id
            """,
            len(rows),
        )
        sem = asyncio.Semaphore(self.settings.scheduler_claim_limit)

        async def _one(source_id: int) -> RefreshOutcome:
            async with sem:
                return await refresh_source(
                    source_id, self.client, run_id=run_id, settings=self.settings
                )

        results = await asyncio.gather(*[_one(row["id"]) for row in rows])
        await _finalize_run(run_id, results)

    async def start_refresh_all(self) -> int:
        """High-parallelism refresh of every source."""
        # Cancel any in-flight full refresh so we don't double-run
        if self._full_refresh_task and not self._full_refresh_task.done():
            self._full_refresh_cancel.set()
            self._full_refresh_task.cancel()
            try:
                await self._full_refresh_task
            except (asyncio.CancelledError, Exception):
                pass

        self._full_refresh_cancel.clear()
        rows = await db.fetch("SELECT id FROM sources ORDER BY id")
        run_id = await db.fetchval(
            """
            INSERT INTO crawl_runs (trigger, companies_total)
            VALUES ('manual', $1)
            RETURNING id
            """,
            len(rows),
        )
        await bus.publish(
            "refresh_progress",
            {
                "run_id": run_id,
                "status": "run_started",
                "companies_total": len(rows),
            },
        )
        self._full_refresh_task = asyncio.create_task(
            self._run_manual(int(run_id), [r["id"] for r in rows]),
            name=f"manual-refresh-{run_id}",
        )
        return int(run_id)

    async def start_refresh_company(self, company_id: int) -> int:
        """
        Priority refresh for one company.
        Runs immediately even if a full refresh is in progress (does not wait for it).
        """
        source = await db.fetchrow(
            "SELECT id FROM sources WHERE company_id = $1",
            company_id,
        )
        if not source:
            raise ValueError(f"No source for company_id={company_id}")

        run_id = await db.fetchval(
            """
            INSERT INTO crawl_runs (trigger, companies_total)
            VALUES ('manual_one', 1)
            RETURNING id
            """,
        )
        company = await db.fetchrow("SELECT name FROM companies WHERE id = $1", company_id)
        await bus.publish(
            "refresh_progress",
            {
                "run_id": run_id,
                "status": "run_started",
                "companies_total": 1,
                "priority": True,
                "company_id": company_id,
                "company_name": company["name"] if company else None,
            },
        )

        async def _run() -> None:
            async with self._priority_lock:
                outcome = await refresh_source(
                    source["id"],
                    self.client,
                    run_id=run_id,
                    settings=self.settings,
                )
                await _finalize_run(run_id, [outcome])
                await bus.publish(
                    "refresh_progress",
                    {
                        "run_id": run_id,
                        "status": "refresh_complete",
                        "priority": True,
                        "company_id": company_id,
                    },
                )

        asyncio.create_task(_run(), name=f"priority-refresh-{company_id}")
        return int(run_id)

    async def _run_manual(self, run_id: int, source_ids: list[int]) -> None:
        try:
            sem = asyncio.Semaphore(self.settings.manual_concurrency)
            results: list[RefreshOutcome] = []

            async def _one(source_id: int) -> RefreshOutcome | None:
                if self._full_refresh_cancel.is_set():
                    return None
                async with sem:
                    if self._full_refresh_cancel.is_set():
                        return None
                    return await refresh_source(
                        source_id, self.client, run_id=run_id, settings=self.settings
                    )

            # Process in large concurrent batches
            gathered = await asyncio.gather(
                *[_one(sid) for sid in source_ids],
                return_exceptions=True,
            )
            for item in gathered:
                if isinstance(item, RefreshOutcome):
                    results.append(item)
                elif isinstance(item, Exception) and not isinstance(item, asyncio.CancelledError):
                    log.warning("manual refresh item failed: %s", item)

            if results:
                await _finalize_run(run_id, results)
            await bus.publish(
                "refresh_progress",
                {"run_id": run_id, "status": "refresh_complete"},
            )
        except asyncio.CancelledError:
            await db.execute(
                "UPDATE crawl_runs SET finished_at = now() WHERE id = $1 AND finished_at IS NULL",
                run_id,
            )
            raise


async def _finalize_run(run_id: int, results: list[RefreshOutcome]) -> None:
    ok = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    jobs_new = sum(r.jobs_new for r in results)
    jobs_closed = sum(r.jobs_closed for r in results)

    for r in results:
        await db.execute(
            """
            INSERT INTO crawl_run_companies (
                run_id, source_id, status, started_at, finished_at,
                jobs_found, jobs_new, was_baseline, error, duration_ms
            ) VALUES (
                $1, $2, $3, now() - make_interval(secs => $4::double precision / 1000.0), now(),
                $5, $6, $7, $8, $9
            )
            ON CONFLICT (run_id, source_id) DO UPDATE SET
                status = EXCLUDED.status,
                finished_at = EXCLUDED.finished_at,
                jobs_found = EXCLUDED.jobs_found,
                jobs_new = EXCLUDED.jobs_new,
                was_baseline = EXCLUDED.was_baseline,
                error = EXCLUDED.error,
                duration_ms = EXCLUDED.duration_ms
            """,
            run_id,
            r.source_id,
            "ok" if r.ok else "error",
            float(r.duration_ms),
            r.jobs_found,
            r.jobs_new,
            r.was_baseline,
            (r.error or "").replace("\x00", "")[:2000] if r.error else None,
            r.duration_ms,
        )

    await db.execute(
        """
        UPDATE crawl_runs SET
            finished_at = now(),
            companies_ok = $2,
            companies_failed = $3,
            jobs_new = $4,
            jobs_closed = $5
        WHERE id = $1
        """,
        run_id,
        ok,
        failed,
        jobs_new,
        jobs_closed,
    )
