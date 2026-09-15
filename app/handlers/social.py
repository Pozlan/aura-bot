from datetime import timedelta
import random

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import ECONOMY
from app.database.db import get_session
from app.services.economy import (
    get_or_create_user, get_or_create_group, get_or_create_state,
    parse_amount, InvalidAmount, InsufficientBalance, available_balance, adjust_balance, format_amount,
)
from app.services.response_engine import react
from app.utils.targeting import resolve_reply_target
from app.utils.time import utcnow
from app.utils.html_esc import esc
from app.services.premium_emoji import pe

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(Command("tip"))
async def tip(message: Message):
    target = resolve_reply_target(message)
    if target is None:
        await message.reply("reply to the person you want to tip. usage: /tip 500")
        return
    if target.id == message.from_user.id:
        await message.reply("you can't tip yourself.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("usage: /tip &lt;amount&gt; (as a reply)")
        return
    try:
        amount = parse_amount(parts[1])
    except InvalidAmount as e:
        await message.reply(f"can't do that: {e}")
        return

    async with get_session() as session:
        sender = message.from_user
        await get_or_create_user(session, sender.id, sender.full_name, sender.username)
        await get_or_create_user(session, target.id, target.full_name, target.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        sender_state = await get_or_create_state(session, sender.id, message.chat.id)
        target_state = await get_or_create_state(session, target.id, message.chat.id)

        if amount > available_balance(sender_state):
            await message.reply("you don't have that much available.")
            return

        try:
            await adjust_balance(session, sender_state, -amount, "tip", ref=f"to {target.id}", group_id=message.chat.id)
            await adjust_balance(session, target_state, amount, "tip", ref=f"from {sender.id}", group_id=message.chat.id)
        except InsufficientBalance:
            await message.reply("you don't have that much available.")
            return

    await message.reply(f"{pe('bff')} <b>Tip sent</b>\n{esc(sender.full_name)} sent <b>{format_amount(amount)}</b> to {esc(target.full_name)}.")


@router.message(Command("protect"))
async def protect(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Open door · 5m", callback_data="door:open")
    ]])

    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)

        now = utcnow()
        if state.protected_until and state.protected_until > now:
            # BUG FIX: this branch replied with plain text and no keyboard
            # at all, so re-checking /protect while already protected made
            # the door button disappear entirely instead of always being
            # reachable. Same button, same behavior, every time -- unless
            # the door's already open right now, in which case there's
            # nothing to open.
            door_open = bool(state.door_open_until and state.door_open_until > now)
            if door_open:
                await message.reply(f"{pe('save')} you're already protected, and your door's already open right now.")
            else:
                await message.reply(f"{pe('save')} you're already protected.", reply_markup=kb)
            return

        state.protected_until = now + timedelta(seconds=ECONOMY.PROTECTION_DURATION_S)
        state.door_open_until = None
        state.hits_since_protection = 0  # fresh 2-hit allowance for their next unprotected stretch

    await message.reply(
        f"{pe('save')} <b>Protection active</b>\nno one can rob you for <b>24h</b>.\nyou're also locked out of robbery.",
        reply_markup=kb,
    )


@router.message(Command("rob"))
async def rob(message: Message):
    target = resolve_reply_target(message)
    if target is None:
        await message.reply("reply to the person you want to rob. usage: /rob (as a reply)")
        return
    if target.id == message.from_user.id:
        await message.reply("you can't rob yourself.")
        return

    async with get_session() as session:
        robber = message.from_user
        await get_or_create_user(session, robber.id, robber.full_name, robber.username)
        await get_or_create_user(session, target.id, target.full_name, target.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        robber_state = await get_or_create_state(session, robber.id, message.chat.id)
        target_state = await get_or_create_state(session, target.id, message.chat.id)

        now = utcnow()

        # Bug fix: a protected robber (door closed) must not be able to rob
        # anyone -- previously only the TARGET's protection was checked here,
        # so a protected player could freely rob others while staying safe
        # themselves. Same door-open exception applies to the robber as to
        # the target: open your own door for 5 min to be allowed to rob.
        robber_protected = bool(robber_state.protected_until and robber_state.protected_until > now)
        robber_door_open = bool(robber_state.door_open_until and robber_state.door_open_until > now)
        if robber_protected and not robber_door_open:
            await message.reply(f"{pe('save')} you're protected right now, open your door first if you want to rob.")
            return

        is_protected = bool(target_state.protected_until and target_state.protected_until > now)
        door_open = bool(target_state.door_open_until and target_state.door_open_until > now)

        if is_protected and not door_open:
            text = react("protection", target=esc(target.full_name))
            await message.reply(text)
            return

        # Hard cap -- once hit this many times since their last /protect,
        # a player is fully unrobbable until they run /protect again
        # (which resets the counter to 0). No robber cooldown exists
        # anymore, so this is what actually bounds total exposure.
        if target_state.hits_since_protection >= ECONOMY.ROBBERY_MAX_HITS_BEFORE_PROTECTION:
            await message.reply(f"{esc(target.full_name)} needs to run /protect before anyone can rob them again.")
            return

        # Auto grace period -- blocks EVERY robber, not just this one, for
        # a short while after the target's last successful hit. This is
        # what actually stops a pile-on: /protect requires the player to
        # remember to activate it, this doesn't.
        in_grace = bool(target_state.robbed_immune_until and target_state.robbed_immune_until > now)
        if in_grace:
            await message.reply(f"{esc(target.full_name)} just got hit, they're laying low. try someone else.")
            return

        if target_state.balance < ECONOMY.ROBBERY_MIN_TARGET_BALANCE:
            await message.reply(f"{esc(target.full_name)} doesn't have enough on them to be worth robbing.")
            return

        # No random miss chance -- an unprotected target has no way to
        # resist. Protection (see checks above) is the only defense.
        pct = random.uniform(ECONOMY.ROBBERY_STEAL_PCT_MIN, ECONOMY.ROBBERY_STEAL_PCT_MAX)
        steal = max(1, int(target_state.balance * pct))
        await adjust_balance(session, target_state, -steal, "rob", ref=f"robbed by {robber.id}", group_id=message.chat.id)
        await adjust_balance(session, robber_state, steal, "rob", ref=f"robbed {target.id}", group_id=message.chat.id)
        robber_state.robberies_success += 1
        target_state.times_robbed += 1
        target_state.hits_since_protection += 1
        target_state.robbed_immune_until = now + timedelta(seconds=ECONOMY.ROBBERY_VICTIM_GRACE_S)
        text = react("rob_success", robber=esc(robber.full_name), target=esc(target.full_name), amount=steal)

    await message.reply(text)


@router.callback_query(F.data == "door:open")
async def open_door(callback: CallbackQuery):
    async with get_session() as session:
        user = callback.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, callback.message.chat.id, callback.message.chat.title or "")
        state = await get_or_create_state(session, user.id, callback.message.chat.id)
        now = utcnow()
        if not state.protected_until or state.protected_until <= now:
            await callback.answer("you're not protected right now.", show_alert=True)
            return
        if state.door_open_until and state.door_open_until > now:
            await callback.answer("the door's already open.", show_alert=True)
            return

        state.door_open_until = now + timedelta(seconds=ECONOMY.DOOR_DURATION_S)

    await callback.message.reply("<b>Door open</b>\nyou're exposed for <b>5 minutes</b>.\nyou can rob others now.")
    await callback.answer()
