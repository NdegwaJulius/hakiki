import os
import ssl as _ssl
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv

load_dotenv()

_raw_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./hakiki.db").strip()

# Railway / Heroku provide postgresql:// — SQLAlchemy async needs postgresql+asyncpg://
if _raw_url.startswith("postgresql://") or _raw_url.startswith("postgres://"):
    _raw_url = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1) \
                       .replace("postgresql://", "postgresql+asyncpg://", 1)

DATABASE_URL = _raw_url

# PostgreSQL needs a slightly different engine config
_is_postgres = DATABASE_URL.startswith("postgresql")

# Supabase pooler uses a self-signed cert: encrypt the connection but skip verification
_pg_ssl_ctx = _ssl.create_default_context()
_pg_ssl_ctx.check_hostname = False
_pg_ssl_ctx.verify_mode = _ssl.CERT_NONE

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    **({
        "poolclass": NullPool,
        "connect_args": {
            "ssl": _pg_ssl_ctx,
            "statement_cache_size": 0,
        },
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
