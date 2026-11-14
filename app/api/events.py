from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.realtime import bus

router = APIRouter(tags=["events"])


@router.get("/events")
async def events_stream(request: Request) -> StreamingResponse:
    async def gen():
        async for chunk in bus.sse_stream():
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
