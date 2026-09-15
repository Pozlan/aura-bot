import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.models import Base
from app.services.economy import (
    get_or_create_user, get_or_create_group, get_or_create_state,
    adjust_balance, reserve, available_balance, InsufficientBalance,
)
from app.services.challenge import create_challenge, accept_challenge, resolve_challenge, ChallengeError


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
async def test_balance_never_goes_negative(session_maker):
    async with session_maker() as session:
        state = await _make_player(session, 1)
        with pytest.raises(InsufficientBalance):
            await adjust_balance(session, state, -(state.balance + 1), "test")
        await session.commit()


@pytest.mark.asyncio
async def test_reservation_blocks_double_spend(session_maker):
    async with session_maker() as session:
        state = await _make_player(session, 1)
        full = available_balance(state)
        await reserve(session, state, full)
        with pytest.raises(InsufficientBalance):
            await reserve(session, state, 1)
        await session.commit()


@pytest.mark.asyncio
async def test_challenge_full_lifecycle_pays_winner(session_maker):
    async with session_maker() as session:
        creator = await _make_player(session, 1, name="Creator")
        acceptor = await _make_player(session, 2, name="Acceptor")
        start_creator, start_acceptor = creator.balance, acceptor.balance

        challenge = await create_challenge(session, 1, "rps", 1, 1000)
        await accept_challenge(session, challenge.id, 2)
        await resolve_challenge(session, challenge, winner_id=1)
        await session.commit()

        creator = await get_or_create_state(session, 1, 1)
        acceptor = await get_or_create_state(session, 2, 1)
        assert creator.balance == start_creator + 1000
        assert acceptor.balance == start_acceptor - 1000
        assert creator.reserved == 0 and acceptor.reserved == 0


@pytest.mark.asyncio
async def test_cannot_accept_own_challenge(session_maker):
    async with session_maker() as session:
        await _make_player(session, 1, name="Creator")
        challenge = await create_challenge(session, 1, "rps", 1, 1000)
        with pytest.raises(ChallengeError):
            await accept_challenge(session, challenge.id, 1)
        await session.commit()


@pytest.mark.asyncio
async def test_accept_works_after_fresh_session_reload(session_maker):
    """Regression test: a bug where SQLite drops tzinfo on datetime round-trip
    made expires_at comparisons crash — but only when the row was re-read
    from disk in a NEW session, exactly what happens on every real Telegram
    command. Reusing one session (like the other tests here) doesn't
    exercise this path, so this test deliberately opens fresh sessions."""
    async with session_maker() as session:
        await _make_player(session, 1, name="Creator")
        await _make_player(session, 2, name="Acceptor")
        challenge = await create_challenge(session, 1, "rps", 1, 1000)
        await session.commit()
        challenge_id = challenge.id

    async with session_maker() as session:
        # fresh session -> Challenge.expires_at is read back from disk here,
        # this is exactly where the naive/aware mismatch used to crash
        challenge = await accept_challenge(session, challenge_id, 2)
        await resolve_challenge(session, challenge, winner_id=2)
        await session.commit()


@pytest.mark.asyncio
async def test_cannot_double_accept(session_maker):
    async with session_maker() as session:
        await _make_player(session, 1, name="Creator")
        await _make_player(session, 2, name="A")
        await _make_player(session, 3, name="B")
        challenge = await create_challenge(session, 1, "rps", 1, 1000)
        await accept_challenge(session, challenge.id, 2)
        with pytest.raises(ChallengeError):
            await accept_challenge(session, challenge.id, 3)
        await session.commit()


@pytest.mark.asyncio
async def test_draw_returns_both_wagers(session_maker):
    async with session_maker() as session:
        creator = await _make_player(session, 1, name="Creator")
        acceptor = await _make_player(session, 2, name="Acceptor")
        start_creator, start_acceptor = creator.balance, acceptor.balance

        challenge = await create_challenge(session, 1, "rps", 1, 1000)
        await accept_challenge(session, challenge.id, 2)
        await resolve_challenge(session, challenge, winner_id=None)
        await session.commit()

        creator = await get_or_create_state(session, 1, 1)
        acceptor = await get_or_create_state(session, 2, 1)
        assert creator.balance == start_creator
        assert acceptor.balance == start_acceptor
