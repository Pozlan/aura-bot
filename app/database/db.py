from contextlib import asynccontextmanager
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database.models import Base

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# (table, column, SQL type) -- columns added to a model AFTER the bot was
# already deployed with real data. create_all only creates missing TABLES,
# never adds a missing COLUMN to a table that already exists, so a new
# column here would silently do nothing against an existing database
# without this. No Alembic yet (see init_db docstring), so this is the
# lightweight stand-in: checked once at startup, adds the column if it's
# missing, does nothing if it's already there. Never removes or renames.
_PENDING_COLUMNS = [
    ("player_state", "total_wagered", "BIGINT DEFAULT 0"),
    ("player_state", "equipped_gift_id", "INTEGER REFERENCES gifts(id)"),
    ("player_state", "robbed_immune_until", "TIMESTAMP"),
    ("player_state", "hits_since_protection", "INTEGER DEFAULT 0"),
    ("player_state", "streak_count", "INTEGER DEFAULT 0"),
    ("player_state", "streak_best", "INTEGER DEFAULT 0"),
    ("player_state", "last_streak_at", "TIMESTAMP"),
    ("player_state", "streak_milestone_claimed", "INTEGER DEFAULT 0"),
]


async def init_db() -> None:
    """MVP schema bootstrap. Swap for Alembic once the schema stabilizes —
    create_all is fine pre-launch but won't handle future migrations safely."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    await _ensure_global_group()


async def _ensure_global_group() -> None:
    """player_state.group_id carries a FK to groups.id. Balances/streaks/
    protection now live under the economy.GLOBAL_ID sentinel (0) instead of
    a real Telegram chat id (see economy.get_or_create_state), so that
    sentinel needs an actual row in `groups` or every wallet creation fails
    the FK constraint. Runs once at startup, idempotent."""
    from app.database.models import Group
    from app.services.economy import GLOBAL_ID
    async with SessionLocal() as session:
        existing = await session.get(Group, GLOBAL_ID)
        if existing is None:
            session.add(Group(id=GLOBAL_ID, title="GLOBAL"))
            await session.commit()


def _add_missing_columns(sync_conn) -> None:
    inspector = inspect(sync_conn)
    for table, column, coltype in _PENDING_COLUMNS:
        if table not in inspector.get_table_names():
            continue  # brand-new database -- create_all already made it correctly
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))


@asynccontextmanager
async def get_session():
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
