from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base


def make_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_schema(session_factory: async_sessionmaker[AsyncSession]) -> None:
    engine = session_factory.kw["bind"]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

