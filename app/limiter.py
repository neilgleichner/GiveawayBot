from redis.asyncio import Redis


class RateLimiter:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def allowed(self, key: str, limit: int, window_seconds: int) -> bool:
        """Fixed-window limiter. Redis failure is surfaced, never silently bypassed."""
        redis_key = f"giveaway:rate:{key}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds, nx=True)
            count, _ = await pipe.execute()
        return int(count) <= limit

