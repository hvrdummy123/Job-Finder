from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["refresh"])


@router.post("/refresh")
async def refresh_all(request: Request) -> dict[str, int]:
    scheduler = request.app.state.scheduler
    run_id = await scheduler.start_refresh_all()
    return {"run_id": run_id}


@router.post("/refresh/company/{company_id}")
async def refresh_company(company_id: int, request: Request) -> dict[str, int | bool]:
    """Priority refresh — only this company, runs immediately."""
    scheduler = request.app.state.scheduler
    try:
        run_id = await scheduler.start_refresh_company(company_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"run_id": run_id, "company_id": company_id, "priority": True}
