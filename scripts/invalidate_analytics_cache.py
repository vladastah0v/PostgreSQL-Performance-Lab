import asyncio

from app.core.config import Settings
from app.core.redis import create_redis, invalidate_analytics_cache


async def main() -> None:
    settings = Settings()
    async with create_redis(settings) as redis:
        await invalidate_analytics_cache(redis, settings)
    print("Analytics cache version updated")


if __name__ == "__main__":
    asyncio.run(main())
