"""Redis client construction and connectivity checks."""

from redis.asyncio import Redis

from industry_platform.core.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    """Create the process-wide asynchronous Redis client."""

    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
        decode_responses=True,
    )


async def check_redis_connection(client: Redis) -> None:
    """Raise when Redis does not answer PING successfully."""

    if await client.ping() is not True:
        raise ConnectionError("Redis PING returned an unexpected response")
