"""
Regression coverage for the naive/aware datetime bug that broke /rps,
/protect, and repeat-cooldown messages. Each test here deliberately opens
a FRESH session per step — exactly how a real bot request works (every
Telegram command gets its own get_session()) — because reusing one
session hides this bug via SQLAlchemy's identity map.
"""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.models import Base
from app.services import cooldown as cd
from app.services.economy import get_or_create_user, get_or_create_group, get_or_create_state
from app.utils.time import utcnow


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_cooldown_check_survives_fresh_session(session_maker):
    async with session_maker() as session:
        await cd.set_cooldown(session, 1, 1, "farm", 3600)
        await session.commit()

    async with session_maker() as session:
        # fresh session -> available_at read back from disk, this is where
        # a naive/aware mismatch used to raise TypeError
        remaining = await cd.check(session, 1, 1, "farm")
        assert remaining is not None
        assert remaining.total_seconds() > 0


@pytest.mark.asyncio
async def test_cooldown_expires_correctly(session_maker):
    async with session_maker() as session:
        await cd.set_cooldown(session, 1, 1, "farm", -10)  # already in the past
        await session.commit()

    async with session_maker() as session:
        remaining = await cd.check(session, 1, 1, "farm")
        assert remaining is None


@pytest.mark.asyncio
async def test_protection_timestamp_survives_fresh_session(session_maker):
    async with session_maker() as session:
        await get_or_create_user(session, 1, "P", None)
        await get_or_create_group(session, 1, "g")
        state = await get_or_create_state(session, 1, 1)
        state.protected_until = utcnow()
        await session.commit()

    async with session_maker() as session:
        state = await get_or_create_state(session, 1, 1)
        # this comparison used to crash when protected_until came back naive
        # and `now` was built as tz-aware
        assert state.protected_until <= utcnow()
