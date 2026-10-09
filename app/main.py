import json
import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from .db import ensure_indexes, now
from .limiter import rate_limit
from .routes import analytics, auth, teams, work
from .schemas import ErrorOut
from .security import COOKIE, digest


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {"time": now().isoformat(), "level": record.levelname, "event": record.getMessage()}
        )


logger = logging.getLogger("tasko")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def error(request, status, code, message, details=None):
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": getattr(request.state, "request_id", ""),
                "details": details or [],
            }
        },
    )


def create_app(settings=None, db=None, redis=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        mongo = None
        cache = redis
        try:
            if db is None:
                mongo = AsyncMongoClient(
                    settings.mongo_uri,
                    tz_aware=True,
                    serverSelectionTimeoutMS=3000,
                    connectTimeoutMS=3000,
                    socketTimeoutMS=10000,
                    maxPoolSize=100,
                )
                app.state.db = mongo[settings.mongo_database]
                await mongo.admin.command("ping")
            else:
                app.state.db = db
            if cache is None:
                cache = Redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
            app.state.redis = cache
            await cache.ping()
            if settings.create_indexes_on_startup:
                await ensure_indexes(app.state.db)
            yield
        finally:
            if mongo is not None:
                await mongo.close()
            if redis is None and cache is not None:
                await cache.aclose()

    app = FastAPI(
        title="Tasko API",
        version="1.0.0",
        lifespan=lifespan,
        description="Team-scoped task management. Login returns a CSRF token; send X-CSRF-Token on authenticated writes.",
        responses={code: {"model": ErrorOut} for code in (401, 403, 404, 409, 422, 429, 503)},
    )
    app.state.settings = settings

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        response = error(request, exc.status_code, f"http_{exc.status_code}", str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not reflect submitted passwords, tokens or entire input payloads.
        details = [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
        return error(request, 422, "validation_error", "Request validation failed", details)

    @app.exception_handler(DuplicateKeyError)
    async def conflict(request, exc):
        return error(request, 409, "conflict", "A unique record already exists")

    @app.exception_handler(PyMongoError)
    async def database_error(request, exc):
        logger.error("database_unavailable")
        return error(request, 503, "dependency_unavailable", "Database temporarily unavailable")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        request.state.request_id = uuid4().hex
        started = time.monotonic()
        headers = {}
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                if origin is not None and origin not in settings.allowed_origins:
                    raise HTTPException(403, "Origin not allowed")
            if request.url.path.startswith("/api/") and request.method != "OPTIONS":
                # Invalid/random cookies never get their own allowance: validate before keying by user.
                token = request.cookies.get(COOKIE, "")
                session = None
                if token and len(token) <= 128:
                    session = await app.state.db.sessions.find_one(
                        {"_id": digest(token), "expires_at": {"$gt": now()}}
                    )
                ip = request.client.host if request.client else "unknown"
                auth_route = request.url.path in {"/api/v1/auth/login", "/api/v1/auth/register"}
                identity = (
                    "auth:" + ip
                    if auth_route
                    else ("user:" + session["user_id"] if session else "ip:" + ip)
                )
                limit = settings.auth_rate_limit if auth_route else settings.api_rate_limit
                allowed, headers = await rate_limit(app.state.redis, identity, limit)
                if not allowed:
                    response = error(request, 429, "rate_limited", "Too many requests")
                else:
                    headers.pop("Retry-After", None)
                    response = await call_next(request)
            else:
                response = await call_next(request)
        except HTTPException as exc:
            response = error(request, exc.status_code, f"http_{exc.status_code}", str(exc.detail))
        except (RedisError, PyMongoError):
            response = error(
                request, 503, "dependency_unavailable", "Required service temporarily unavailable"
            )
        except Exception:
            logger.error("unhandled_request_error")
            response = error(request, 500, "internal_error", "Unexpected server error")
        response.headers.update(headers)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        route = request.scope.get("route")
        logger.info(
            json.dumps(
                {
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "route": getattr(route, "path", "unmatched"),
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
        ],
    )
    for router in (auth.router, teams.router, work.router, analytics.router):
        app.include_router(router, prefix="/api/v1")

    @app.get("/health/live", tags=["Health"])
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["Health"])
    async def ready():
        await app.state.db.command("ping")
        await app.state.redis.ping()
        return {"status": "ready"}

    return app


app = create_app()
