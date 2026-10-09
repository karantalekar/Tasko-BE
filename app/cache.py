import logging
from uuid import uuid4

from redis.exceptions import RedisError

logger = logging.getLogger("tasko")


async def invalidate(redis, team_id):
    # Random generations prevent stale builders from overwriting a newer generation.
    # Expire generation keys to bound storage; generation initialization uses NX.
    try:
        await redis.set(f"tasko:analytics-generation:{team_id}", uuid4().hex, ex=86400)
    except RedisError:
        logger.warning("analytics_cache_invalidation_failed")


async def generation(redis, team_id):
    key = f"tasko:analytics-generation:{team_id}"
    await redis.set(key, uuid4().hex, nx=True, ex=86400)
    return await redis.get(key)
