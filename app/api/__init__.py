from fastapi import APIRouter

from app.api import companies, events, health, jobs, refresh, role_titles, searches, visits

api_router = APIRouter(prefix="/api")
api_router.include_router(companies.router)
api_router.include_router(jobs.router)
api_router.include_router(searches.router)
api_router.include_router(refresh.router)
api_router.include_router(events.router)
api_router.include_router(health.router)
api_router.include_router(visits.router)
api_router.include_router(role_titles.router)
