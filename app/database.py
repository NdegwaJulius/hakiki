import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

_raw_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./hakiki.db")

# Railway / Heroku provide postgresql:// — SQLAlchemy async needs postgresql+asyncpg://
if _raw_url.startswith("postgresql://") or _raw_url.startswith("postgres://"):
    _raw_url = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1) \
                       .replace("postgresql://", "postgresql+asyncpg://", 1)

DATABASE_URL = _raw_url

# PostgreSQL needs a slightly different engine config
_is_postgres = DATABASE_URL.startswith("postgresql")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    **({
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
    } if _is_postgres else {
        "connect_args": {"check_same_thread": False},
    })
)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
