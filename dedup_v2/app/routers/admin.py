from fastapi import APIRouter

from app.services import redis_client

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.delete("/flush")
async def flush_redis():
    """Clear all Redis data completely."""
    r = redis_client.get_redis()
    await r.flushdb()
    return {"status": "flushed"}
