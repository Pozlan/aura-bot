"""
Owner-only tools. Restricted to OWNER_IDS in .env (see app/config.py) --
these bypass all normal economy rules on purpose, so they should never be
reachable by a regular player. Every use is still logged to the
Transaction ledger like any other balance change, so it's auditable.
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from app.config import settings
from app.database.db import get_session
from app.database.models import User
from app.services.challenge import force_cancel_all, reconcile_reservations
from app.services.economy import (
    format_amount, parse_amount, InvalidAmount, get_or_create_user,
    get_or_create_state, adjust_balance,
)
from app.utils.custom_emoji import extract_custom_emoji_ids
from app.utils.html_esc import esc
from app.utils.targeting import resolve_reply_target
from sqlalchemy import select

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(Command("reset"))
async def reset(message: Message):
    """Force-refunds every open (pending/accepted) challenge bot-wide right
    now, regardless of its 3-min timer. Escape hatch for "something's stuck
    and I want it cleared immediately" -- not something to run routinely,
    since it also cancels any game that's genuinely still in progress and
    about to be picked up normally."""
    if message.from_user.id not in settings.owner_id_set:
        return  # silently ignore -- no error text, so it doesn't hint the command exists

    async with get_session() as session:
        cleared = await force_cancel_all(session)

    if not cleared:
        await message.reply("nothing was open. no challenges to clear.")
        return

    total = sum(c.wager for c in cleared)
    await message.reply(
        f"cleared {len(cleared)} open challenge(s), {format_amount(total)} total refunded."
    )


@router.message(Command("reconcile"))
async def reconcile(message: Message):
    """Fixes 'locked in an open challenge' amounts that have no actual open
    challenge behind them anymore -- data drift that /reset can't touch,
    since /reset only cancels challenges that still exist. This recomputes
    every player's reserved amount from scratch against what's genuinely
    still open right now."""
    if message.from_user.id not in settings.owner_id_set:
        return  # silently ignore -- no error text, so it doesn't hint the command exists

    async with get_session() as session:
        fixed = await reconcile_reservations(session)
        if fixed:
            names = {
                u.id: u.display_name
                for u in (await session.execute(select(User).where(User.id.in_([f[0] for f in fixed])))).scalars()
            }

    if not fixed:
        await message.reply("nothing to fix, every reserved amount already matches reality.")
        return

    lines = [f"fixed {len(fixed)} player(s):"]
    for user_id, old, new in fixed:
        name = esc(names.get(user_id, str(user_id)))
        lines.append(f"{name}: {format_amount(old)} to {format_amount(new)}")
    await message.reply("\n".join(lines))


@router.message(Command("deduct"))
async def deduct(message: Message):
    """Reply to someone + /deduct <amount> to subtract from their balance.
    Goes through adjust_balance like any normal transaction -- shows up in
    their ledger as kind="admin", and can never push them below 0. Quiet by
    design: replies nothing for non-owners, and even for the owner it's a
    plain text reply, not an announcement -- meant to correct something
    (like an exploited /hunt payout) without turning it into a public callout."""
    if message.from_user.id not in settings.owner_id_set:
        return  # silently ignore -- no error text, so it doesn't hint the command exists

    target = resolve_reply_target(message)
    if target is None:
        await message.reply("reply to the person you want to deduct from.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("usage: reply with /deduct <amount>")
        return
    try:
        amount = parse_amount(parts[1])
    except InvalidAmount as e:
        await message.reply(f"can't do that: {e}")
        return

    async with get_session() as session:
        await get_or_create_user(session, target.id, target.full_name, target.username)
        state = await get_or_create_state(session, target.id)
        actual = min(amount, state.balance)  # never push below 0
        await adjust_balance(session, state, -actual, "admin", ref=f"deducted by {message.from_user.id}", group_id=message.chat.id)
        new_balance = state.balance

    await message.reply(f"deducted {format_amount(actual)} from {esc(target.full_name)}. new balance: {format_amount(new_balance)}")


@router.message(Command("grant"))
async def grant(message: Message):
    """Reply to someone (or send standalone to grant yourself) + /grant
    <amount>. This is the command that got removed earlier for creating aura
    from nothing -- reintroduced deliberately, so use sparingly. Still goes
    through adjust_balance, so it's at least ledger-logged as kind="admin"
    rather than an invisible manual edit."""
    if message.from_user.id not in settings.owner_id_set:
        return  # silently ignore -- no error text, so it doesn't hint the command exists

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("usage: /grant <amount> (reply to someone to grant them instead of yourself)")
        return
    try:
        amount = parse_amount(parts[1])
    except InvalidAmount as e:
        await message.reply(f"can't do that: {e}")
        return

    target = resolve_reply_target(message)
    recipient = target if target else message.from_user

    async with get_session() as session:
        await get_or_create_user(session, recipient.id, recipient.full_name, recipient.username)
        state = await get_or_create_state(session, recipient.id)
        await adjust_balance(session, state, amount, "admin", ref=f"granted by {message.from_user.id}", group_id=message.chat.id)
        new_balance = state.balance

    await message.reply(f"granted {format_amount(amount)} to {esc(recipient.full_name)}. new balance: {format_amount(new_balance)}")


@router.message(Command("emojiid"))
async def emojiid(message: Message):
    if message.from_user.id not in settings.owner_id_set:
        return  # silently ignore -- no error text, so it doesn't hint the command exists

    ids = extract_custom_emoji_ids(message)
    if not ids:
        await message.reply(
            "no custom emoji found. reply to a message with one, or send it "
            "right after the command (e.g. <code>/emojiid</code> replying to one)."
        )
        return

    lines = ["<b>Custom emoji ID(s)</b>"] + [f"<code>{cid}</code>" for cid in ids]
    await message.reply("\n".join(lines))
