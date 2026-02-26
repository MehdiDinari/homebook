from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.middleware.rate_limit import RedisRateLimitMiddleware
from app.websockets.notifications import notifications_ws_router
from app.websockets.rooms import chat_ws_router
from app.websockets.sessions import session_ws_router


configure_logging()

app = FastAPI(title=settings.app_name, default_response_class=ORJSONResponse, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RedisRateLimitMiddleware)

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(chat_ws_router, prefix=settings.ws_prefix)
app.include_router(notifications_ws_router, prefix=settings.ws_prefix)
app.include_router(session_ws_router, prefix=settings.ws_prefix)

Instrumentator().instrument(app).expose(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
