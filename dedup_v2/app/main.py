from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.services import redis_client
from app.routers import alerts, admin, dashboard, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: connect to Redis
    await redis_client.connect()
    yield
    # Shutdown: disconnect from Redis
    await redis_client.disconnect()


app = FastAPI(
    title="STIX Alert Deduplication Engine",
    description="Enterprise three-layer dedup engine for STIX 2.1 security alerts",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(alerts.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
