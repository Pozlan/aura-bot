"""
rps.py resolved PvP results with ~15 lines of duplicated bookkeeping
(release reservations, transfer balance, record streaks, log GameHistory,
fetch names). Coin/Dice/HighLow need the exact same bookkeeping, so it's
factored out here instead of copy-pasted three more times.
"""
from app.database.models import GameHistory, User, Challenge, PlayerState
from app.services.challenge import resolve_challenge
from app.services.economy import get_or_create_state, record_result, adjust_balance
from app.utils.html_esc import esc


async def finalize_pvp(session, challenge: Challenge, winner_id: int | None) -> dict:
    """winner_id=None is a draw. Returns display info the handler needs to
    build its message — this function never touches Telegram objects.
    creator_name/acceptor_name are pre-escaped for the bot's HTML parse
    mode, since they come straight from a stored Telegram display name."""
    creator_id, acceptor_id = challenge.creator_id, challenge.acceptor_id
    await resolve_challenge(session, challenge, winner_id)

    creator_state = await get_or_create_state(session, creator_id, challenge.group_id)
    acceptor_state = await get_or_create_state(session, acceptor_id, challenge.group_id)
    record_result(creator_state, None if winner_id is None else winner_id == creator_id)
    record_result(acceptor_state, None if winner_id is None else winner_id == acceptor_id)

    session.add(GameHistory(
        group_id=challenge.group_id, game=challenge.game, mode="pvp",
        player_id=creator_id, opponent_id=acceptor_id, wager=challenge.wager,
        result="draw" if winner_id is None else ("win" if winner_id == creator_id else "loss"),
    ))

    creator_name = esc((await session.get(User, creator_id)).display_name)
    acceptor_name = esc((await session.get(User, acceptor_id)).display_name)
    return {
        "creator_id": creator_id, "acceptor_id": acceptor_id,
        "creator_name": creator_name, "acceptor_name": acceptor_name,
        "creator_state": creator_state, "acceptor_state": acceptor_state,
        "winner_id": winner_id, "wager": challenge.wager,
    }


async def finalize_house(session, game: str, state: PlayerState, group_id: int, wager: int, won: bool | None) -> None:
    """won=None is a draw against the house — no balance change."""
    if won is not None:
        await adjust_balance(session, state, wager if won else -wager, "game", ref=f"{game} house", group_id=group_id)
    record_result(state, won)
    session.add(GameHistory(
        group_id=group_id, game=game, mode="house", player_id=state.user_id, opponent_id=None,
        wager=wager, result="draw" if won is None else ("win" if won else "loss"),
    ))
