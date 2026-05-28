from fastapi import APIRouter

from app.api.v1.endpoints import (
    activity_events,
    ai,
    auth,
    dashboard,
    employees,
    recommendations,
    teams,
)

api_router = APIRouter()


@api_router.get("/health", tags=["health"])
async def api_health_check() -> dict[str, str]:
    return {"status": "ok"}


api_router.include_router(employees.router)
api_router.include_router(activity_events.router)
api_router.include_router(ai.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(recommendations.router)
api_router.include_router(teams.router)
