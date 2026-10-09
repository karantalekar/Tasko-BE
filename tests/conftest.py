"""Default tests never connect to real databases. Optional integration uses a random test DB."""

import os
from uuid import uuid4

import mongomock
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from pymongo import AsyncMongoClient
from redis.asyncio import Redis

from app.config import Settings
from app.main import create_app


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def sort(self, *args):
        self.cursor = self.cursor.sort(*args)
        return self

    def skip(self, value):
        self.cursor = self.cursor.skip(value)
        return self

    def limit(self, value):
        self.cursor = self.cursor.limit(value)
        return self

    async def to_list(self, length=None):
        return list(self.cursor)[:length]

    def __aiter__(self):
        self.iterator = iter(self.cursor)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration:
            raise StopAsyncIteration


class Collection:
    def __init__(self, collection):
        self.collection = collection

    def find(self, *args, **kwargs):
        return Cursor(self.collection.find(*args, **kwargs))

    async def aggregate(self, pipeline, **kwargs):
        return Cursor(self.collection.aggregate(pipeline))

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            return getattr(self.collection, name)(*args, **kwargs)

        return call


class Database:
    def __init__(self):
        self.db = mongomock.MongoClient(tz_aware=True).tasko_test

    def __getattr__(self, name):
        return Collection(self.db[name])

    def __getitem__(self, name):
        return Collection(self.db[name])

    async def command(self, name):
        return {"ok": 1}


@pytest_asyncio.fixture
async def services():
    mongo = None
    database_name = "tasko_test_" + uuid4().hex
    if os.getenv("TEST_MONGO_URI"):
        mongo = AsyncMongoClient(os.environ["TEST_MONGO_URI"], tz_aware=True)
        db = mongo[database_name]
        # Test Redis must be a dedicated disposable instance. No flush commands are used.
        redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    else:
        db, redis = Database(), FakeRedis(decode_responses=True)
    yield db, redis
    if mongo is not None:
        assert database_name.startswith("tasko_test_")
        await mongo.drop_database(database_name)
        await mongo.close()
    await redis.aclose()


@pytest_asyncio.fixture
async def app(services):
    db, redis = services
    application = create_app(
        Settings(cookie_secure=False, auth_rate_limit=1000, api_rate_limit=10000), db, redis
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def account(client, name="Owner", role="owner", email_domain="example.com"):
    email = name.lower() + uuid4().hex[:8] + "@" + email_domain
    body = {"name": name, "email": email, "password": "Safe-test-password-123", "role": role}
    response = await client.post("/api/v1/auth/register", json=body)
    assert response.status_code == 201, response.text
    user = response.json()
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": body["password"]}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return user


async def setup_team(client):
    user = await account(client)
    team = await client.post("/api/v1/teams", json={"name": "Engineering"})
    assert team.status_code == 201, team.text
    base = "/api/v1/teams/" + team.json()["id"]
    project = await client.post(base + "/projects", json={"name": "Tasko", "status": "in_progress"})
    assert project.status_code == 201, project.text
    return user, team.json(), project.json(), base
