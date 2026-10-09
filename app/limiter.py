import math

from .security import digest

# Redis TIME is authoritative across API hosts; fixed windows are atomic.
SCRIPT = """
local t = redis.call('TIME')
local now = tonumber(t[1])
local window = tonumber(ARGV[2])
local bucket = math.floor(now / window)
local key = KEYS[1] .. ':' .. bucket
local count = redis.call('INCR', key)
if count == 1 then redis.call('EXPIRE', key, window + 1) end
local reset = (bucket + 1) * window
return {count, reset, reset - now}
"""


async def rate_limit(redis, identity, limit, window=60):
    count, reset, retry = await redis.eval(SCRIPT, 1, "tasko:rl:" + digest(identity), limit, window)
    return int(count) <= limit, {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(0, limit - int(count))),
        "X-RateLimit-Reset": str(reset),
        "Retry-After": str(max(1, math.ceil(retry))),
    }
