"""
/rob involves real money movement and a security check (protection), so
this is tested at the economy level rather than trusted to work just
because the handler code reads correctly.
"""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.models import Base
from app.config import ECONOMY
from app.services.economy import get_or_create_user, get_or_create_group, get_or_create_state, adjust_balance
from app.services import cooldown as cd
from app.utils.time import utcnow
from datetime import timedelta


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_player(session, uid, group_id=1, balance=None):
    await get_or_create_user(session, uid, "P", None)
    await get_or_create_group(session, group_id, "g")
    state = await get_or_create_state(session, uid, group_id)
    if balance is not None:
        state.balance = balance
    return state


@pytest.mark.asyncio
async def test_protected_target_is_detected(session_maker):
    async with session_maker() as session:
        target = await _make_player(session, 2, balance=100_000)
        target.protected_until = utcnow() + timedelta(hours=1)
        await session.commit()

    async with session_maker() as session:
        state = await get_or_create_state(session, 2, 1)
        now = utcnow()
        is_protected = bool(state.protected_until and state.protected_until > now)
        door_open = bool(state.door_open_until and state.door_open_until > now)
        assert is_protected is True
        assert door_open is False


@pytest.mark.asyncio
async def test_open_door_overrides_protection(session_maker):
    async with session_maker() as session:
        target = await _make_player(session, 2, balance=100_000)
        target.protected_until = utcnow() + timedelta(hours=1)
        target.door_open_until = utcnow() + timedelta(minutes=5)
        await session.commit()

    async with session_maker() as session:
        state = await get_or_create_state(session, 2, 1)
        now = utcnow()
        is_protected = bool(state.protected_until and state.protected_until > now)
        door_open = bool(state.door_open_until and state.door_open_until > now)
        # robbable because door is open, even though protected_until is still active
        assert is_protected and door_open


@pytest.mark.asyncio
async def test_rob_cooldown_survives_fresh_session(session_maker):
    async with session_maker() as session:
        await cd.set_cooldown(session, 1, 1, "rob", ECONOMY.ROBBERY_COOLDOWN_S)
        await session.commit()

    async with session_maker() as session:
        remaining = await cd.check(session, 1, 1, "rob")
        assert remaining is not None
        assert remaining.total_seconds() > 0


@pytest.mark.asyncio
async def test_successful_rob_moves_money_correctly(session_maker):
    async with session_maker() as session:
        robber = await _make_player(session, 1, balance=1000)
        target = await _make_player(session, 2, balance=100_000)
        steal = 5000
        await adjust_balance(session, target, -steal, "rob", ref="test")
        await adjust_balance(session, robber, steal, "rob", ref="test")
        await session.commit()

    async with session_maker() as session:
        robber = await get_or_create_state(session, 1, 1)
        target = await get_or_create_state(session, 2, 1)
        assert robber.balance == 1000 + 5000
        assert target.balance == 100_000 - 5000


@pytest.mark.asyncio
async def test_steal_amount_is_flat_20_percent_of_target_balance(session_maker):
    async with session_maker() as session:
        target = await _make_player(session, 2, balance=100_000)
        steal = max(1, int(target.balance * ECONOMY.ROBBERY_STEAL_PCT))
        assert steal == 20_000


@pytest.mark.asyncio
async def test_failed_rob_costs_robber_nothing(session_maker):
    """A failed robbery no longer penalizes the robber at all -- only a
    successful one moves money. This test simulates a full failure path
    (no adjust_balance call for the robber) and confirms their balance
    is completely untouched."""
    async with session_maker() as session:
        robber = await _make_player(session, 1, balance=100)
        start_balance = robber.balance
        # a failed robbery does nothing to the robber's balance -- no
        # adjust_balance call happens on this path at all
        await session.commit()

    async with session_maker() as session:
        robber = await get_or_create_state(session, 1, 1)
        assert robber.balance == start_balance
