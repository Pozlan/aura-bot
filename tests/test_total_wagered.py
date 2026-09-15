import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.models import Base
from app.services.economy import get_or_create_user, get_or_create_group, get_or_create_state, reserve, release_reservation


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_player(session, uid, group_id=1):
    await get_or_create_user(session, uid, "P", None)
    await get_or_create_group(session, group_id, "g")
    return await get_or_create_state(session, uid, group_id)


@pytest.mark.asyncio
async def test_reserve_increments_total_wagered(session_maker):
    async with session_maker() as session:
        state = await _make_player(session, 1)
        assert state.total_wagered == 0
        await reserve(session, state, 1000)
        await session.commit()

    async with session_maker() as session:
        state = await get_or_create_state(session, 1, 1)
        assert state.total_wagered == 1000


@pytest.mark.asyncio
async def test_total_wagered_accumulates_across_multiple_bets(session_maker):
    async with session_maker() as session:
        state = await _make_player(session, 1)
        await reserve(session, state, 1000)
        await release_reservation(session, state, 1000)
        await reserve(session, state, 500)
        await session.commit()

    async with session_maker() as session:
        state = await get_or_create_state(session, 1, 1)
        assert state.total_wagered == 1500  # both bets counted, even after the first resolved


@pytest.mark.asyncio
async def test_release_does_not_undo_total_wagered(session_maker):
    """Releasing a reservation (draw, or a challenge expiring) should NOT
    undo the fact that the wager was placed -- total_wagered tracks
    lifetime activity, not current exposure."""
    async with session_maker() as session:
        state = await _make_player(session, 1)
        await reserve(session, state, 2000)
        await release_reservation(session, state, 2000)
        await session.commit()

    async with session_maker() as session:
        state = await get_or_create_state(session, 1, 1)
        assert state.total_wagered == 2000
        assert state.reserved == 0
