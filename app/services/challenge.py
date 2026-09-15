"""
Spec section 13 + 34: one reusable challenge system for RPS/Coin/Dice/HighLow
instead of duplicating accept/expire/validate logic per game.

Security model (section 34) — every one of these is enforced here, not
trusted from Telegram callback data:
  - challenge exists
  - not expired
  - not already accepted/resolved
  - acceptor isn't the creator
  - acceptor has enough available balance
  - wager is reserved (not deducted) until resolution, so a crash between
    accept and resolve never loses or duplicates aura
"""
import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Challenge, PlayerState
from app.services.economy import get_or_create_state, reserve, release_reservation, InsufficientBalance, GLOBAL_ID
from app.config import ECONOMY
from app.utils.time import utcnow as _now


class ChallengeError(ValueError):
    pass


async def create_challenge(
    session: AsyncSession, group_id: int, game: str, creator_id: int, wager: int, state: dict | None = None
) -> Challenge:
    creator_state = await get_or_create_state(session, creator_id, group_id)
    await reserve(session, creator_state, wager)  # raises InsufficientBalance if not enough

    challenge = Challenge(
        group_id=group_id,
        game=game,
        creator_id=creator_id,
        wager=wager,
        status="pending",
        expires_at=_now() + timedelta(seconds=ECONOMY.CHALLENGE_EXPIRATION_S),
        state=json.dumps(state or {}),
    )
    session.add(challenge)
    await session.flush()
    return challenge


async def get_challenge(session: AsyncSession, challenge_id: int) -> Challenge | None:
    return await session.get(Challenge, challenge_id)


async def accept_challenge(session: AsyncSession, challenge_id: int, acceptor_id: int) -> Challenge:
    challenge = await session.get(Challenge, challenge_id)
    if challenge is None:
        raise ChallengeError("this challenge no longer exists.")
    if challenge.status != "pending":
        raise ChallengeError("this challenge has already been settled.")
    if challenge.expires_at < _now():
        await _expire_one(session, challenge)
        raise ChallengeError("this challenge expired.")
    if acceptor_id == challenge.creator_id:
        raise ChallengeError("you can't accept your own challenge.")

    acceptor_state = await get_or_create_state(session, acceptor_id, challenge.group_id)
    try:
        await reserve(session, acceptor_state, challenge.wager)
    except InsufficientBalance:
        raise ChallengeError("not enough aura to accept this.")

    challenge.acceptor_id = acceptor_id
    challenge.status = "accepted"
    # Reset the clock: RPS is the one game where "accepted" isn't final --
    # both players still have to separately pick rock/paper/scissors after
    # this. Give that phase its own fresh window instead of inheriting
    # whatever was left of the original accept-me countdown.
    challenge.expires_at = _now() + timedelta(seconds=ECONOMY.CHALLENGE_EXPIRATION_S)
    await session.flush()
    return challenge


async def resolve_challenge(
    session: AsyncSession, challenge: Challenge, winner_id: int | None
) -> None:
    """winner_id=None means a draw — both reservations released, no transfer."""
    creator_state = await get_or_create_state(session, challenge.creator_id, challenge.group_id)
    acceptor_state = await get_or_create_state(session, challenge.acceptor_id, challenge.group_id)
    pot = challenge.wager * 2

    await release_reservation(session, creator_state, challenge.wager)
    await release_reservation(session, acceptor_state, challenge.wager)

    from app.services.economy import adjust_balance
    if winner_id is None:
        pass  # nothing to transfer, reservations already released = wagers returned
    elif winner_id == challenge.creator_id:
        await adjust_balance(session, creator_state, challenge.wager, "game", ref=f"{challenge.game}#{challenge.id} win", group_id=challenge.group_id)
        await adjust_balance(session, acceptor_state, -challenge.wager, "game", ref=f"{challenge.game}#{challenge.id} loss", group_id=challenge.group_id)
    else:
        await adjust_balance(session, acceptor_state, challenge.wager, "game", ref=f"{challenge.game}#{challenge.id} win", group_id=challenge.group_id)
        await adjust_balance(session, creator_state, -challenge.wager, "game", ref=f"{challenge.game}#{challenge.id} loss", group_id=challenge.group_id)

    challenge.status = "resolved"


async def reconcile_reservations(session: AsyncSession) -> list[tuple[int, int, int]]:
    """Manual repair for /reconcile (admin.py). Recomputes every player's
    `reserved` from what's ACTUALLY still open in the challenges table right
    now, instead of trusting the stored number. Fixes drift like: a
    reservation that lost its backing challenge somewhere along the way (a
    data-migration edge case, a bug since patched, whatever) and has been
    sitting there with nothing left to ever expire it -- `/reset` can't
    touch these since there's no open challenge for it to cancel.

    Returns (user_id, old_reserved, new_reserved) for every player whose
    stored value didn't match reality, so it can be reported precisely
    rather than "everyone's reserved got zeroed, hope that was right."."""
    open_stmt = select(Challenge).where(Challenge.status.in_(("pending", "accepted")))
    open_challenges = list((await session.execute(open_stmt)).scalars())

    actual: dict[int, int] = {}
    for c in open_challenges:
        actual[c.creator_id] = actual.get(c.creator_id, 0) + c.wager
        if c.acceptor_id is not None:
            actual[c.acceptor_id] = actual.get(c.acceptor_id, 0) + c.wager

    stmt = select(PlayerState).where(PlayerState.group_id == GLOBAL_ID, PlayerState.reserved != 0)
    states = list((await session.execute(stmt)).scalars())

    fixed = []
    for state in states:
        correct = actual.get(state.user_id, 0)
        if state.reserved != correct:
            fixed.append((state.user_id, state.reserved, correct))
            state.reserved = correct
    return fixed


async def cancel_own_pending(session: AsyncSession, user_id: int) -> list[Challenge]:
    """Public, self-service version of /reset -- for /cancel (pvp_common.py).
    Deliberately much narrower than force_cancel_all: only cancels the
    CALLER's own challenges, and ONLY if still "pending" (nobody's accepted
    yet). Never touches "accepted" challenges, even the caller's own --
    once someone else has money on the line, bailing isn't a unilateral
    decision anymore. That restriction is what makes this safe to expose
    to every player instead of owner-only."""
    stmt = select(Challenge).where(Challenge.status == "pending", Challenge.creator_id == user_id)
    own_pending = list((await session.execute(stmt)).scalars())
    for challenge in own_pending:
        challenge.status = "cancelled"
        await _refund_creator(session, challenge)
    return own_pending


async def force_cancel_all(session: AsyncSession) -> list[Challenge]:
    """Manual escape hatch for /reset (admin.py) -- same refund logic as
    cancel_expired, but with NO expiry check at all. Clears every open
    challenge bot-wide right now, regardless of its timer. Meant for
    "something's stuck and I don't want to wait/debug it", not routine use."""
    stmt = select(Challenge).where(Challenge.status.in_(("pending", "accepted")))
    open_challenges = list((await session.execute(stmt)).scalars())
    for challenge in open_challenges:
        await _expire_one(session, challenge)
    return open_challenges


async def cancel_expired(session: AsyncSession) -> list[Challenge]:
    """Background sweep (bot.py, every 30s): refunds every expired challenge
    in the whole database, regardless of who's involved. Covers BOTH states
    that can go stale:
      - "pending": nobody accepted in time -> refund the creator only.
      - "accepted": RPS is the one game where accepting doesn't immediately
        resolve -- both players still have to pick a move. If one never
        does, this used to sit forever with BOTH wagers reserved."""
    stmt = select(Challenge).where(
        Challenge.status.in_(("pending", "accepted")), Challenge.expires_at < _now()
    )
    expired = list((await session.execute(stmt)).scalars())
    for challenge in expired:
        await _expire_one(session, challenge)
    return expired


async def cancel_expired_for_user(session: AsyncSession, user_id: int) -> list[Challenge]:
    """Second safety net, independent of the background loop. Scoped to one
    player and cheap enough to run on every balance touch -- called from
    economy.get_or_create_state, so ANY time this player's balance is read
    or written anywhere in the bot (/bal, hosting a new game, tipping,
    robbing...), their own stale reservations clear first. This means a
    stuck reservation can't survive past the next time you touch your
    balance, even if the background loop were somehow not running."""
    stmt = select(Challenge).where(
        Challenge.status.in_(("pending", "accepted")),
        Challenge.expires_at < _now(),
        (Challenge.creator_id == user_id) | (Challenge.acceptor_id == user_id),
    )
    expired = list((await session.execute(stmt)).scalars())
    for challenge in expired:
        await _expire_one(session, challenge)
    return expired


async def _expire_one(session: AsyncSession, challenge: Challenge) -> None:
    # Status flips to "expired" FIRST, before either refund call. Both
    # refunds go through get_or_create_state, which now ALSO self-heals
    # expired challenges for whichever player it's fetching (see
    # economy.py). If status were still "pending"/"accepted" when that
    # nested self-heal runs, its query would find THIS SAME challenge
    # again and recurse forever. Flipping status first means the nested
    # query's status filter no longer matches it.
    was_accepted = challenge.status == "accepted"
    challenge.status = "expired"
    await _refund_creator(session, challenge)
    if was_accepted:
        acceptor_state = await get_or_create_state(session, challenge.acceptor_id, challenge.group_id)
        await release_reservation(session, acceptor_state, challenge.wager)


async def _refund_creator(session: AsyncSession, challenge: Challenge) -> None:
    creator_state = await get_or_create_state(session, challenge.creator_id, challenge.group_id)
    await release_reservation(session, creator_state, challenge.wager)
  
