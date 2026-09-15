"""
Regression coverage for the lightweight column-migration mechanism in
app/database/db.py. A new column added to a model (like total_wagered)
does nothing against a database that already has that table -- SQLAlchemy's
create_all only creates missing TABLES. This got caught by hand before
being wired up correctly; these tests pin the fix in place.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database.models import Base
from app.database.db import _add_missing_columns


@pytest.mark.asyncio
async def test_migration_adds_missing_column_without_losing_data():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # simulate the pre-existing deployment: drop the new column, matching
        # what a database created before total_wagered existed looks like
        await conn.execute(text("ALTER TABLE player_state RENAME TO player_state_old"))
        await conn.execute(text("""
            CREATE TABLE player_state (
                id INTEGER PRIMARY KEY, user_id BIGINT, group_id BIGINT,
                balance BIGINT, reserved BIGINT, win_streak INTEGER, loss_streak INTEGER,
                wins INTEGER, losses INTEGER, robberies_success INTEGER, robberies_failed INTEGER,
                times_robbed INTEGER, protected_until DATETIME, door_open_until DATETIME, updated_at DATETIME
            )
        """))
        await conn.execute(text("""
            INSERT INTO player_state SELECT id, user_id, group_id, balance, reserved, win_streak,
                loss_streak, wins, losses, robberies_success, robberies_failed, times_robbed,
                protected_until, door_open_until, updated_at FROM player_state_old
        """))
        await conn.execute(text("DROP TABLE player_state_old"))
        await conn.execute(text("INSERT INTO player_state (user_id, group_id, balance) VALUES (999, 1, 1000000000)"))

    async with engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)

    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT balance, total_wagered FROM player_state WHERE user_id = 999"))
        row = result.first()
        assert row.balance == 1_000_000_000  # existing data survived
        assert row.total_wagered == 0  # new column added with a safe default

    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_is_idempotent():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # column already exists (fresh create_all includes it) -- running the
    # migration again must not error
    async with engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)
        await conn.run_sync(_add_missing_columns)
    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_skips_nonexistent_tables_safely():
    """A brand-new database has no tables yet at the point create_all runs
    first -- the migration must not error just because a table isn't
    there to check yet."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)  # runs against an empty DB
    await engine.dispose()
