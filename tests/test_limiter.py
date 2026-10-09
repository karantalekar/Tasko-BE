import asyncio
from uuid import uuid4

from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError

from app.config import Settings
from app.limiter import rate_limit
from app.main import create_app


async def test_atomic_limit_across_connections():
    server = FakeServer()
    a, b = (
        FakeRedis(server=server, decode_responses=True),
        FakeRedis(server=server, decode_responses=True),
    )
    # Lua executes the actual production algorithm, shared by independent connections.
    identity = uuid4().hex
    results = await asyncio.gather(
        *(rate_limit(a if i % 2 else b, identity, 5, window=3600) for i in range(30))
    )
    assert sum(allowed for allowed, _ in results) == 5
    assert all(int(headers["X-RateLimit-Remaining"]) >= 0 for _, headers in results)
    assert (
        await a.ttl(
            "tasko:rl:"
            + __import__("app.security", fromlist=["digest"]).digest(identity)
            + ":"
            + str(int((await a.time())[0]) // 3600)
        )
        > 0
    )
    await a.aclose()
    await b.aclose()


async def test_http_429_and_fail_closed(services):
    db, redis = services
    application = create_app(Settings(cookie_secure=False, api_rate_limit=2), db, redis)
    async with application.router.lifespan_context(application):
        async with AsyncClient(
            transport=ASGITransport(app=application, client=(uuid4().hex, 123)),
            base_url="http://test",
        ) as client:
            assert (await client.get("/api/v1/users/me")).status_code == 401
            assert (await client.get("/api/v1/users/me")).status_code == 401
            blocked = await client.get("/api/v1/users/me")
            assert blocked.status_code == 429
            assert int(blocked.headers["Retry-After"]) >= 1
            assert blocked.headers["X-RateLimit-Remaining"] == "0"
            assert blocked.json()["error"]["code"] == "rate_limited"


async def test_redis_failure_is_503(client, app, monkeypatch):
    async def failure(*args, **kwargs):
        raise ConnectionError("not exposed")

    monkeypatch.setattr(app.state.redis, "eval", failure)
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 503
    assert "not exposed" not in response.text
    assert (await client.get("/health/live")).status_code == 200
