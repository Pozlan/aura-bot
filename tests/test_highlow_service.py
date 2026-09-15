import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.models import Base, HighLowRun
from app.services.economy import get_or_create_user, get_or_create_group, get_or_create_state, available_balance
from app.services.highlow_service import start_run, resolve_guess, HighLowError


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
async def test_start_run_reserves_wager_and_first_card_in_range(session_maker):
    async with session_maker() as session:
        state = await _make_player(session, 1)
        start_balance = state.balance
        run = await start_run(session, 1, 1, 1000)
        await session.commit()
        assert run.status == "active"
        assert 2 <= run.current_card <= 12  # first card is always 2-12, never 1 or 13

    async with session_maker() as session:
        state = await get_or_create_state(session, 1, 1)
        assert state.balance == start_balance  # not deducted yet, only reserved
        assert available_balance(state) == start_balance - 1000


@pytest.mark.asyncio
async def test_win_pays_out_and_releases_reservation(session_maker):
    import app.services.highlow_service as svc

    async with session_maker() as session:
        state = await _make_player(session, 1)
        start_balance = state.balance
        run = await start_run(session, 1, 1, 1000)
        run.current_card = 2
        await session.commit()
        run_id = run.id

    async with session_maker() as session:
        run = await session.get(HighLowRun, run_id)
        original_draw = svc.engine.draw_second_card
        svc.engine.draw_second_card = lambda exclude: 10
        try:
            result = await resolve_guess(session, run, "higher")
        finally:
            svc.engine.draw_second_card = original_draw
        await session.commit()
        assert result["won"] is True
        assert result["net"] == 1000  # flat 2x -- profit exactly equals the wager, regardless of odds

    async with session_maker() as session:
        state = await get_or_create_state(session, 1, 1)
        assert state.balance == start_balance + result["net"]
        assert state.reserved == 0
        assert state.wins == 1


@pytest.mark.asyncio
async def test_payout_is_flat_2x_regardless_of_odds(session_maker):
    """A win pays exactly wager-as-profit whether the guess was easy
    (card=2, higher, 11/12 favorable) or hard (card=11, higher, 2/12
    favorable) -- no probability scaling anymore."""
    import app.services.highlow_service as svc

    async with session_maker() as session:
        await _make_player(session, 1)
        easy_run = await start_run(session, 1, 1, 500)
        easy_run.current_card = 2
        await session.commit()
        easy_run_id = easy_run.id

    async with session_maker() as session:
        run = await session.get(HighLowRun, easy_run_id)
        original_draw = svc.engine.draw_second_card
        svc.engine.draw_second_card = lambda exclude: 3  # any card > 2 wins "higher"
        try:
            easy_result = await resolve_guess(session, run, "higher")
        finally:
            svc.engine.draw_second_card = original_draw
        await session.commit()

    async with session_maker() as session:
        hard_run = await start_run(session, 1, 1, 500)
        hard_run.current_card = 11
        await session.commit()
        hard_run_id = hard_run.id

    async with session_maker() as session:
        run = await session.get(HighLowRun, hard_run_id)
        original_draw = svc.engine.draw_second_card
        svc.engine.draw_second_card = lambda exclude: 12  # the only card > 11 besides 13
        try:
            hard_result = await resolve_guess(session, run, "higher")
        finally:
            svc.engine.draw_second_card = original_draw
        await session.commit()

    assert easy_result["net"] == 500
    assert hard_result["net"] == 500
    assert easy_result["net"] == hard_result["net"]


@pytest.mark.asyncio
async def test_loss_deducts_wager_fully(session_maker):
    import app.services.highlow_service as svc

    async with session_maker() as session:
        state = await _make_player(session, 1)
        start_balance = state.balance
        run = await start_run(session, 1, 1, 1000)
        run.current_card = 2
        await session.commit()
        run_id = run.id

    async with session_maker() as session:
        run = await session.get(HighLowRun, run_id)
        original_draw = svc.engine.draw_second_card
        svc.engine.draw_second_card = lambda exclude: 1  # forces "higher" to be wrong
        try:
            result = await resolve_guess(session, run, "higher")
        finally:
            svc.engine.draw_second_card = original_draw
        await session.commit()
        assert result["won"] is False
        assert result["net"] == -1000

    async with session_maker() as session:
        state = await get_or_create_state(session, 1, 1)
        assert state.balance == start_balance - 1000
        assert state.reserved == 0
        assert state.losses == 1


@pytest.mark.asyncio
async def test_second_card_never_equals_first_card(session_maker):
    """No ties by construction -- run this a bunch of times since it's
    randomized, any single tie is a bug."""
    async with session_maker() as session:
        await _make_player(session, 1)
        for i in range(50):
            run = await start_run(session, 1, 1, 100)
            result = await resolve_guess(session, run, "higher" if run.current_card < 12 else "lower")
            assert result["first_card"] != result["second_card"]


@pytest.mark.asyncio
async def test_cannot_guess_higher_on_max_first_card(session_maker):
    # first card can never actually BE 13 (range is 2-12), but the guard
    # should still hold if current_card is ever manually set to 13
    async with session_maker() as session:
        await _make_player(session, 1)
        run = await start_run(session, 1, 1, 1000)
        run.current_card = 13
        await session.commit()
        run_id = run.id

    async with session_maker() as session:
        run = await session.get(HighLowRun, run_id)
        with pytest.raises(HighLowError):
            await resolve_guess(session, run, "higher")


@pytest.mark.asyncio
async def test_cannot_guess_lower_on_min_first_card(session_maker):
    async with session_maker() as session:
        await _make_player(session, 1)
        run = await start_run(session, 1, 1, 1000)
        run.current_card = 1
        await session.commit()
        run_id = run.id

    async with session_maker() as session:
        run = await session.get(HighLowRun, run_id)
        with pytest.raises(HighLowError):
            await resolve_guess(session, run, "lower")


@pytest.mark.asyncio
async def test_cannot_act_on_finished_run(session_maker):
    async with session_maker() as session:
        await _make_player(session, 1)
        run = await start_run(session, 1, 1, 1000)
        await session.commit()
        run_id = run.id

    async with session_maker() as session:
        run = await session.get(HighLowRun, run_id)
        await resolve_guess(session, run, "higher" if engine_can(run) else "lower")
        await session.commit()

    async with session_maker() as session:
        run = await session.get(HighLowRun, run_id)
        with pytest.raises(HighLowError):
            await resolve_guess(session, run, "higher")


def engine_can(run):
    from app.games import highlow as engine
    return engine.can_guess_higher(run.current_card)
