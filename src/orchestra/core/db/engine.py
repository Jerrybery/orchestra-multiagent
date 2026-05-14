"""Database engine and session factory creation."""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base


def create_db_engine(
    database_config=None,
    orchestra_dir=None,
) -> tuple[AsyncEngine, async_sessionmaker]:
    """Create async engine + session factory from config.
    Priority: ORCHESTRA_DATABASE_URL env > database_config dict > SQLite default.
    """
    url = os.environ.get("ORCHESTRA_DATABASE_URL")

    if not url and isinstance(database_config, dict) and "url" in database_config:
        url = database_config["url"]

    if not url:
        if orchestra_dir is None:
            raise ValueError("orchestra_dir required when no database URL is configured")
        db_path = orchestra_dir / "tasks.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{db_path}"

    kwargs = {"echo": False, "pool_pre_ping": True}

    if url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool

    engine = create_async_engine(url, **kwargs)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables. Safe to call on existing databases."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
