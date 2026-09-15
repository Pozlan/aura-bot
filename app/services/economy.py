"""
Two responsibilities that must both be bulletproof:
1. Parsing "500", "25k", "1.5m" into ints, rejecting anything unsafe.
2. Mutating balances atomically so PvP payouts, tips, and gambling can
   never double-pay, double-charge, or race each other.

Every write in here happens inside the caller's `get_session()` transaction —
these functions take a session, they don't open their own, so a challenge
accept + payout can be committed as one atomic unit by the handler.
"""
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.premium_emoji import pe

from app.database.models import PlayerState, Transaction, User, Group
from app.config import ECONOMY

_SUFFIX_MULT = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}
_AMOUNT_RE = re.compile(r"^(\d+(?:\.\d+)?)([kmbt]?)$", re.IGNORECASE)

# Global-economy migration: balances/reserved/streaks/protection used to be
# stored one row per (user_id, group_id). As of the /gtop update there's a
# single row per user instead, stored under this sentinel group_id so the
# existing PlayerState table/unique-constraint didn't need a schema change.
# Real Telegram group ids are always non-zero (negative for groups/supergroups),
# so 0 can never collide with a real chat.
GLOBAL_ID = 0


class InvalidAmount(ValueError):
    pass


class InsufficientBalance(ValueError):
    pass


def parse_amount(raw: str) -> int:
    """Parses shorthand amounts. Raises InvalidAmount on anything unsafe —
    never silently clamps or guesses."""
    if raw is None:
        raise InvalidAmount("no amount given")
    raw = raw.strip().replace(",", "")
    match = _AMOUNT_RE.match(raw)
    if not match:
        raise InvalidAmount(f"'{raw}' isn't a valid amount")

    number, suffix = match.groups()
    value = float(number)
    if suffix:
        value *= _SUFFIX_MULT[suffix.lower()]

    amount = int(round(value))
    if amount <= 0:
        raise InvalidAmount("amount must be positive")
    if amount > 10**15:
        raise InvalidAmount("amount is absurdly large")
    return amount


def format_amount(n: int) -> str:
    """Plain comma-separated display with the aura custom emoji as the
    unit symbol instead of the word 'aura': 1000 -> '1,000 <symbol>'."""
    return f"{n:,} {pe('aura')}"


async def get_or_create_user(session: AsyncSession, user_id: int, display_name: str, username: str | None) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, display_name=display_name, username=username)
        session.add(user)
        await session.flush()
    else:
        user.display_name = display_name
        user.username = username
    return user


async def get_or_create_group(session: AsyncSession, group_id: int, title: str) -> Group:
    group = await session.get(Group, group_id)
    if group is None:
        group = Group(id=group_id, title=title)
        session.add(group)
        await session.flush()
    return group


async def get_or_create_state(session: AsyncSession, user_id: int, group_id: int | None = None) -> PlayerState:
    """Returns the player's single GLOBAL wallet (balance, reserved, streaks,
    robbery stats, protection). `group_id` is accepted so every existing call
    site (which passes message.chat.id) keeps working unchanged, but it's no
    longer used to select the row -- every group now reads/writes the same
    wallet. See GLOBAL_ID above.

    Also self-heals this player's own expired challenge reservations every
    time it's called (deferred import avoids a circular import with
    challenge.py, which itself imports get_or_create_state). This means
    every /bal check, every new wager, every tip/rob touches this first --
    a stuck reservation can't survive past the moment you next do anything,
    independent of whether the periodic background sweep (bot.py) is alive."""
    stmt = select(PlayerState).where(PlayerState.user_id == user_id, PlayerState.group_id == GLOBAL_ID)
    state = (await session.execute(stmt)).scalar_one_or_none()
    if state is None:
        state = PlayerState(user_id=user_id, group_id=GLOBAL_ID, balance=ECONOMY.STARTING_BALANCE)
        session.add(state)
        await session.flush()

    from app.services.challenge import cancel_expired_for_user
    await cancel_expired_for_user(session, user_id)

    return state


def available_balance(state: PlayerState) -> int:
    """Balance minus whatever's locked in open PvP challenges."""
    return state.balance - state.reserved


async def adjust_balance(
    session: AsyncSession,
    state: PlayerState,
    delta: int,
    kind: str,
    ref: str = "",
    group_id: int | None = None,
) -> None:
    """The ONLY function that should mutate PlayerState.balance.
    Refuses to let balance go negative and logs every change to the
    append-only ledger. Caller controls the transaction boundary.

    `group_id`: since state.group_id is now always the GLOBAL_ID sentinel
    (see get_or_create_state), pass the real chat id here so the Transaction
    ledger still records which group a bet/tip/rob actually happened in.
    Falls back to state.group_id (i.e. GLOBAL_ID) if not given."""
    new_balance = state.balance + delta
    if new_balance < 0:
        raise InsufficientBalance(f"balance {state.balance} cannot cover delta {delta}")
    state.balance = new_balance
    session.add(Transaction(
        group_id=group_id if group_id is not None else state.group_id,
        user_id=state.user_id,
        kind=kind,
        amount=delta,
        balance_after=new_balance,
        ref=ref,
    ))


async def reserve(session: AsyncSession, state: PlayerState, amount: int) -> None:
    """Locks `amount` against the player's balance for an open challenge
    without actually deducting it yet — deduction happens on resolution.

    Every wager, across every game (RPS/Coin/Dice/HighLow), goes through
    this single function to lock in a stake — so it's also the single,
    correct place to track lifetime total wagered, without touching every
    individual game handler."""
    if available_balance(state) < amount:
        raise InsufficientBalance("not enough available balance to reserve")
    state.reserved += amount
    state.total_wagered += amount


async def release_reservation(session: AsyncSession, state: PlayerState, amount: int) -> None:
    state.reserved = max(0, state.reserved - amount)


def record_result(state: PlayerState, won: bool | None) -> None:
    """Updates win/loss streak counters. won=None means draw (streaks untouched)."""
    if won is None:
        return
    if won:
        state.wins += 1
        state.win_streak += 1
        state.loss_streak = 0
    else:
        state.losses += 1
        state.loss_streak += 1
        state.win_streak = 0


@dataclass
class WinTier:
    name: str  # small_win | normal_win | big_win | massive_win


def classify_amount(amount: int) -> str:
    """Buckets a aura amount for both win/loss sizing and wager sizing —
    this is what the response engine keys its intensity off of."""
    if amount >= 5_000_000:
        return "massive"
    if amount >= 500_000:
        return "big"
    if amount >= 25_000:
        return "normal"
    return "small"
