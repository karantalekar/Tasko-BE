"""Run with python -m app.indexes before production traffic."""

import asyncio

from pymongo import AsyncMongoClient

from .config import Settings
from .db import ensure_indexes


async def main():
    settings = Settings()
    client = AsyncMongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        await ensure_indexes(client[settings.mongo_database])
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
