from __future__ import annotations

from typing import Annotated

from fastapi import Request

from app.config import Settings
from app.http_client import HttpClient
from app.scheduler import Scheduler


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_http(request: Request) -> HttpClient:
    return request.app.state.http


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler


SettingsDep = Annotated[Settings, ...]
HttpDep = Annotated[HttpClient, ...]
SchedulerDep = Annotated[Scheduler, ...]
