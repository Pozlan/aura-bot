"""
One-shot game vs house. Unlike coin/dice/rps this never goes through
challenge.py -- there's no second player, just a wager reserved between
the initial /highlow message and the button click that resolves it.
"""
from app.database.models import HighLowRun, GameHistory
from app.games import highlow as engine
from app.services.economy import get_or_create_state, reserve, release_reservation, adjust_balance, record_result


class HighLowError(ValueError):
    pass


async def start_run(session, user_id: int, group_id: int, wager: int) -> HighLowRun:
    state = await get_or_create_state(session, user_id, group_id)
    await reserve(session, state, wager)  # raises InsufficientBalance if not enough
    card = engine.draw_first_card()
    run = HighLowRun(
        user_id=user_id, group_id=group_id, wager=wager,
        multiplier=1.0, current_card=card, rounds_played=0, status="active",
    )
    session.add(run)
    await session.flush()
    return run


async def resolve_guess(session, run: HighLowRun, direction: str) -> dict:
    """direction is 'higher' or 'lower'. Resolves the whole game in one
    shot -- draws the second card, settles the payout, and marks the run
    finished. Returns a dict the handler builds its message from."""
    if run.status != "active":
        raise HighLowError("this game isn't active anymore.")
    if direction == "higher" and not engine.can_guess_higher(run.current_card):
        raise HighLowError("can't guess higher on the top card.")
    if direction == "lower" and not engine.can_guess_lower(run.current_card):
        raise HighLowError("can't guess lower on the bottom card.")

    first_card = run.current_card
    second_card = engine.draw_second_card(exclude=first_card)
    won = engine.check_guess(first_card, second_card, direction)

    state = await get_or_create_state(session, run.user_id, run.group_id)
    await release_reservation(session, state, run.wager)

    if won:
        net = run.wager  # flat 2x: win back exactly what you wagered as profit
        await adjust_balance(session, state, net, "game", ref=f"highlow#{run.id} win", group_id=run.group_id)
    else:
        net = -run.wager
        await adjust_balance(session, state, -run.wager, "game", ref=f"highlow#{run.id} loss", group_id=run.group_id)

    record_result(state, won)
    run.status = "resolved" if won else "busted"
    run.current_card = second_card
    run.multiplier = 2.0 if won else 0.0
    run.rounds_played = 1

    session.add(GameHistory(
        group_id=run.group_id, game="highlow", mode="house",
        player_id=run.user_id, opponent_id=None, wager=run.wager,
        result="win" if won else "loss",
    ))

    return {
        "won": won, "first_card": first_card, "second_card": second_card,
        "net": net, "payout": run.wager + net if won else 0,
    }
