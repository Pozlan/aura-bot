import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.models import Base
from app.services.economy import get_or_create_user, get_or_create_group, get_or_create_state
from app.services.challenge import create_challenge, accept_challenge
from app.services.game_common import finalize_pvp, finalize_house


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_player(session, uid, group_id=1, name="P"):
    await get_or_create_user(session, uid, name, None)
    await get_or_create_group(session, group_id, "g")
    return await get_or_create_state(session, uid, group_id)


@pytest.mark.asyncio
async def test_finalize_pvp_pays_winner_across_fresh_sessions(session_maker):
    async with session_maker() as session:
        creator = await _make_player(session, 1, name="Creator")
        acceptor = await _make_player(session, 2, name="Acceptor")
        start_c, start_a = creator.balance, acceptor.balance
        challenge = await create_challenge(session, 1, "coin", 1, 1000)
        await session.commit()
        challenge_id = challenge.id

    async with session_maker() as session:
        challenge = await accept_challenge(session, challenge_id, 2)
        info = await finalize_pvp(session, challenge, winner_id=1)
        await session.commit()
        assert info["creator_name"] == "Creator"
        assert info["acceptor_name"] == "Acceptor"

    async with session_maker() as session:
        creator = await get_or_create_state(session, 1, 1)
        acceptor = await get_or_create_state(session, 2, 1)
        assert creator.balance == start_c + 1000
        assert acceptor.balance == start_a - 1000
        assert creator.wins == 1 and acceptor.losses == 1


@pytest.mark.asyncio
async def test_finalize_pvp_draw_refunds_both(session_maker):
    async with session_maker() as session:
        creator = await _make_player(session, 1, name="Creator")
        acceptor = await _make_player(session, 2, name="Acceptor")
        start_c, start_a = creator.balance, acceptor.balance
        challenge = await create_challenge(session, 1, "dice", 1, 500)
        await session.commit()
        challenge_id = challenge.id

    async with session_maker() as session:
        challenge = await accept_challenge(session, challenge_id, 2)
        await finalize_pvp(session, challenge, winner_id=None)
        await session.commit()

    async with session_maker() as session:
        creator = await get_or_create_state(session, 1, 1)
        acceptor = await get_or_create_state(session, 2, 1)
        assert creator.balance == start_c
        assert acceptor.balance == start_a


@pytest.mark.asyncio
async def test_finalize_house_win_and_loss(session_maker):
    async with session_maker() as session:
        state = await _make_player(session, 1)
        start = state.balance
        await finalize_house(session, "dice", state, 1, 500, won=True)
        await session.commit()

    async with session_maker() as session:
        state = await get_or_create_state(session, 1, 1)
        assert state.balance == start + 500
        assert state.wins == 1

    async with session_maker() as session:
        state = await get_or_create_state(session, 1, 1)
        await finalize_house(session, "dice", state, 1, 300, won=False)
        await session.commit()

    async with session_maker() as session:
        state = await get_or_create_state(session, 1, 1)
        assert state.balance == start + 500 - 300
        assert state.losses == 1
