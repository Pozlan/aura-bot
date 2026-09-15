"""
/streak -- daily activation streak. Global per player, same as the wallet
(see economy.GLOBAL_ID): one streak across every group, not one per group.

Evaluated lazily on each /streak call, same philosophy as cooldown.py --
no background job. Two bounds, not cooldown.py's one:
  - elapsed < STREAK_WINDOW_S -> too soon, still on cooldown from the last
    activation, streak untouched. Same shape as /farm.
  - STREAK_WINDOW_S <= elapsed < STREAK_BREAK_S -> on time, streak
    continues (streak_count += 1).
  - elapsed >= STREAK_BREAK_S (or no last_streak_at at all) -> too late,
    the streak is broken. Resets to 1 -- this activation starts a new one,
    it doesn't just fail.

Milestone gifts are minted fresh per player (see mint_streak_gift) rather
than drawn from shared Gift stock -- every player who reaches a given
day-count gets their own row, lifetime-earned, tracked via
streak_milestone_claimed so a broken-and-rebuilt streak never re-grants
a milestone already claimed.
"""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ECONOMY
from app.database.models import Gift, PlayerState
from app.utils.time import utcnow as _now


@dataclass
class StreakResult:
    activated: bool                 # False if still on cooldown (too soon)
    remaining: timedelta | None     # set when activated is False
    streak_count: int
    broken: bool                    # True if this activation reset a lapsed streak
    milestone_hit: int | None       # day-count just crossed, if any
    milestone_gift: Gift | None     # the freshly minted gift, if any


async def activate(session: AsyncSession, state: PlayerState) -> StreakResult:
    now = _now()
    broken = False

    if state.last_streak_at is not None:
        elapsed = (now - state.last_streak_at).total_seconds()
        if elapsed < ECONOMY.STREAK_WINDOW_S:
            remaining = timedelta(seconds=ECONOMY.STREAK_WINDOW_S - elapsed)
            return StreakResult(False, remaining, state.streak_count, False, None, None)
        elif elapsed < ECONOMY.STREAK_BREAK_S:
            state.streak_count += 1
        else:
            broken = True
            state.streak_count = 1
    else:
        state.streak_count = 1

    state.last_streak_at = now
    state.streak_best = max(state.streak_best, state.streak_count)

    milestone_hit = None
    milestone_gift = None
    # Milestones are keyed by exact day-count, but a player could in theory
    # jump straight past one only if STREAK_MILESTONES itself changes shape
    # underneath an in-progress streak -- catch that by granting the
    # highest un-claimed milestone at or below the current count, not just
    # an exact match.
    eligible = [d for d in ECONOMY.STREAK_MILESTONES if d <= state.streak_count and d > state.streak_milestone_claimed]
    if eligible:
        milestone_hit = max(eligible)
        state.streak_milestone_claimed = milestone_hit
        milestone_gift = await mint_streak_gift(session, state.user_id, milestone_hit)

    return StreakResult(True, None, state.streak_count, broken, milestone_hit, milestone_gift)


async def mint_streak_gift(session: AsyncSession, user_id: int, milestone_days: int) -> Gift:
    """Creates a brand-new, already-owned Gift row for this player's
    milestone -- NOT drawn from shared /shop stock, so it can never
    collide with another player earning the same milestone. category is
    always "streak", which gifts.get_categories() filters out of /shop
    entirely (see gifts.py) -- this badge is only ever earned, never sold
    or bought."""
    emoji_id = ECONOMY.STREAK_MILESTONES[milestone_days]
    gift = Gift(
        category="streak",
        tier=None,
        emoji_id=emoji_id,
        price=0,
        owner_user_id=user_id,
        purchased_at=_now(),
    )
    session.add(gift)
    await session.flush()  # populate gift.id for the caller's reply text
    return gift
