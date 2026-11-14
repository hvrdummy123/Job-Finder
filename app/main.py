from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import api_router
from app.config import get_settings
from app.http_client import HttpClient
from app.ingest.locations import load_alias_cache
from app.scheduler import Scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings

    await db.init_pool(settings)
    await db.run_migrations(settings)
    await load_alias_cache()

    http = HttpClient(settings)
    await http.start()
    app.state.http = http

    scheduler = Scheduler(settings, http)
    scheduler.start()
    app.state.scheduler = scheduler

    log.info("Job Radar ready on %s:%s", settings.bind_host, settings.bind_port)
    try:
        yield
    finally:
        await scheduler.stop()
        await http.aclose()
        try:
            from app.connectors.playwright_pool import shutdown as pw_shutdown

            await pw_shutdown()
        except Exception:
            pass
        await db.close_pool()


app = FastAPI(title="Personal Job Radar", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)

_web_dist = Path(get_settings().web_dist_dir)
if _web_dist.is_dir():
    assets = _web_dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str = "") -> FileResponse:
        candidate = _web_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_web_dist / "index.html")


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
