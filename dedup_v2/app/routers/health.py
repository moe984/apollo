from fastapi import APIRouter

from app.models import HealthResponse
from app.services import redis_client

router = APIRouter(tags=["health"])


@router.get("/api/v1/health", response_model=HealthResponse)
async def health_check():
    """Liveness check including Redis connectivity."""
    try:
        r = redis_client.get_redis()
        await r.ping()
        redis_status = "connected"
    except Exception as e:
        redis_status = f"error: {e}"

    return HealthResponse(
        status="ok" if redis_status == "connected" else "degraded",
        redis=redis_status,
    )
