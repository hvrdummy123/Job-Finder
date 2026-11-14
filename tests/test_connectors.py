from pathlib import Path

import httpx
import pytest
import respx

from app.connectors.greenhouse import GreenhouseConnector
from app.connectors.util import fingerprint_ids
from app.http_client import HttpClient
from app.models import Company, Source
from app.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
async def client():
    settings = Settings(http_user_agent="PersonalJobRadar-Test/0.1")
    http = HttpClient(settings)
    await http.start()
    yield http
    await http.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_greenhouse_list_and_get(client: HttpClient):
    list_body = (FIXTURES / "greenhouse_jobs.json").read_text()
    detail_body = (FIXTURES / "greenhouse_job_detail.json").read_text()

    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, text=list_body, headers={"content-type": "application/json"})
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/1001").mock(
        return_value=httpx.Response(200, text=detail_body, headers={"content-type": "application/json"})
    )

    company = Company(id=1, name="Acme", careers_url="https://boards.greenhouse.io/acme")
    source = Source(id=1, company_id=1, ats_type="greenhouse", ats_slug="acme")
    connector = GreenhouseConnector()

    result = await connector.list_jobs(company, source, client)
    assert result.complete is True
    assert len(result.stubs) == 2
    assert result.fingerprint == fingerprint_ids(["1001", "1002"])

    draft = await connector.get_job(company, source, result.stubs[0], client)
    assert draft.external_job_id == "1001"
    assert draft.apply_url
    assert "Python" in (draft.description_text or "")
    assert draft.raw.get("id") == 1001


def test_greenhouse_detect():
    connector = GreenhouseConnector()
    detected = connector.detect("https://boards.greenhouse.io/acme")
    assert detected is not None
    assert detected.ats_type == "greenhouse"
    assert detected.ats_slug == "acme"


def test_fingerprint_stable():
    assert fingerprint_ids(["b", "a"]) == fingerprint_ids(["a", "b"])
    assert fingerprint_ids(["a"]) != fingerprint_ids(["a", "b"])
