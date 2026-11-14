from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.http_client import HttpClient
from app.models import Company, DetectResult, FetchResult, JobDraft, JobStub, ListResult, Source


@runtime_checkable
class Connector(Protocol):
    name: str

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult | None: ...

    async def list_jobs(
        self, company: Company, source: Source, client: HttpClient
    ) -> ListResult: ...

    async def get_job(
        self,
        company: Company,
        source: Source,
        stub: JobStub,
        client: HttpClient,
    ) -> JobDraft: ...


class ConnectorRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, Connector] = {}
        self._ordered: list[Connector] = []

    def register(self, connector: Connector) -> None:
        self._by_name[connector.name] = connector
        self._ordered.append(connector)

    def get(self, name: str) -> Connector:
        if name not in self._by_name:
            raise KeyError(f"Unknown connector: {name}")
        return self._by_name[name]

    def get_or_generic(self, ats_type: str) -> Connector:
        if ats_type in self._by_name:
            return self._by_name[ats_type]
        return self._by_name["generic"]

    def detect(self, url: str, response: FetchResult | None = None) -> DetectResult:
        for connector in self._ordered:
            result = connector.detect(url, response)
            if result is not None:
                return result
        return DetectResult(
            ats_type="generic",
            confidence=0.2,
            fallback_stage="unknown",
            needs_playwright=False,
        )

    def all(self) -> list[Connector]:
        return list(self._ordered)


registry = ConnectorRegistry()
