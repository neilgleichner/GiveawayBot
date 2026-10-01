import asyncio
import logging

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis
import structlog

from app.config import get_settings
from app.db import create_schema, make_session_factory
from app.handlers import build_router
from app.limiter import RateLimiter
from app.seed import seed_operators
from app.services import close_expired, start_due


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", level=level.upper())
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        processors=[structlog.processors.TimeStamper(fmt="iso", utc=True), structlog.processors.JSONRenderer()],
    )


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger()
    session_factory = make_session_factory(settings)
    await create_schema(session_factory)
    async with session_factory() as session:
        await seed_operators(session)

    redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    await redis.ping()
    bot = Bot(token=settings.bot_token.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(settings, session_factory, RateLimiter(redis)))

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(start_due, "interval", seconds=20, args=[session_factory], id="activate-giveaways")
    scheduler.add_job(close_expired, "interval", seconds=20, args=[session_factory], id="close-giveaways")
    scheduler.start()
    try:
        # This project uses outbound long polling, so no public application port is required.
        await bot.delete_webhook(drop_pending_updates=False)
        log.info("bot_starting", comment_chat_id=settings.comment_chat_id)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await redis.aclose()
        await bot.session.close()
        await session_factory.kw["bind"].dispose()


if __name__ == "__main__":
    asyncio.run(run())

