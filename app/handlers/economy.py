import random

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import ECONOMY
from app.database.db import get_session
from app.services import cooldown as cd
from app.services.economy import get_or_create_user, get_or_create_group, get_or_create_state, adjust_balance, format_amount
from app.services.response_engine import react, win_category, loss_category
from app.services.premium_emoji import pe

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


async def _prep(message: Message):
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)
        return session, state


@router.message(Command("farm"))
async def farm(message: Message):
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)

        remaining = await cd.check(session, user.id, message.chat.id, "farm")
        if remaining:
            await message.reply(f"{pe('afk')} already claimed. come back in {cd.format_remaining(remaining)}.")
            return

        amount = random.randint(ECONOMY.FARM_MIN, ECONOMY.FARM_MAX)
        await adjust_balance(session, state, amount, "farm", ref="daily claim", group_id=message.chat.id)
        await cd.set_cooldown(session, user.id, message.chat.id, "farm", ECONOMY.FARM_COOLDOWN_S)

    await message.reply(f"{pe('gold')} Daily claimed\n+{format_amount(amount)}\ncome back tomorrow.")


def _work_keyboard(job_names: list[str], owner_id: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=job.title(), callback_data=f"work:{owner_id}:{job}")] for job in job_names]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("work"))
async def work(message: Message):
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        await get_or_create_state(session, user.id, message.chat.id)

        remaining = await cd.check(session, user.id, message.chat.id, "work")
        if remaining:
            await message.reply(f"{pe('afk')} still on shift. try again in {cd.format_remaining(remaining)}.")
            return

    options = random.sample(list(ECONOMY.WORK_JOBS.keys()), k=3)
    await message.reply(
        f"{pe('gold')} <b>Job Center</b>\npick a shift:",
        reply_markup=_work_keyboard(options, message.from_user.id),
    )


@router.callback_query(F.data.startswith("work:"))
async def on_work_choice(callback: CallbackQuery):
    _, owner_id_s, job = callback.data.split(":", maxsplit=2)
    if callback.from_user.id != int(owner_id_s):
        await callback.answer("this isn't your job offer.", show_alert=True)
        return

    async with get_session() as session:
        user = callback.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        state = await get_or_create_state(session, user.id, callback.message.chat.id)

        # re-check cooldown at click time too — closes the gap where two
        # /work messages could otherwise both queue up buttons and double-pay
        remaining = await cd.check(session, user.id, callback.message.chat.id, "work")
        if remaining:
            await callback.answer(f"already on shift, {cd.format_remaining(remaining)} left.", show_alert=True)
            return

        lo, hi = ECONOMY.WORK_JOBS[job]
        amount = random.randint(lo, hi)
        await adjust_balance(session, state, amount, "work", ref=job, group_id=callback.message.chat.id)
        await cd.set_cooldown(session, user.id, callback.message.chat.id, "work", ECONOMY.WORK_COOLDOWN_S)

    await callback.message.edit_text(f"{pe('gold')} Work\nyou worked as a {job}.\n+{format_amount(amount)}")
    await callback.answer()


@router.message(Command("loot"))
async def loot(message: Message):
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)

        remaining = await cd.check(session, user.id, message.chat.id, "loot")
        if remaining:
            await message.reply(f"{pe('afk')} nothing new yet. try again in {cd.format_remaining(remaining)}.")
            return

        await cd.set_cooldown(session, user.id, message.chat.id, "loot", ECONOMY.LOOT_COOLDOWN_S)
        if random.random() < ECONOMY.LOOT_SUCCESS_RATE:
            amount = random.randint(ECONOMY.LOOT_MIN, ECONOMY.LOOT_MAX)
            await adjust_balance(session, state, amount, "loot", ref="found", group_id=message.chat.id)
            text = f"{pe('loot')} Loot\nyou found {format_amount(amount)}."
        else:
            text = f"{pe('loot')} Loot\nnothing useful this time.\ntry again later."

    await message.reply(text)


@router.message(Command("hunt"))
async def hunt(message: Message):
    parts = message.text.split(maxsplit=1)
    from app.services.economy import parse_amount, InvalidAmount, InsufficientBalance, available_balance

    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)

        remaining = await cd.check(session, user.id, message.chat.id, "hunt")
        if remaining:
            await message.reply(f"{pe('afk')} recovering. try again in {cd.format_remaining(remaining)}.")
            return

        try:
            stake = parse_amount(parts[1]) if len(parts) > 1 else ECONOMY.HUNT_MIN_STAKE
        except InvalidAmount as e:
            await message.reply(f"can't do that: {e}")
            return
        if stake < ECONOMY.HUNT_MIN_STAKE:
            await message.reply(f"minimum hunt stake is {format_amount(ECONOMY.HUNT_MIN_STAKE)}.")
            return
        if stake > ECONOMY.HUNT_MAX_STAKE:
            await message.reply(f"max hunt stake is {format_amount(ECONOMY.HUNT_MAX_STAKE)}.")
            return
        if stake > available_balance(state):
            await message.reply("you don't have that much to risk.")
            return

        await cd.set_cooldown(session, user.id, message.chat.id, "hunt", ECONOMY.HUNT_COOLDOWN_S)
        if random.random() < ECONOMY.HUNT_SUCCESS_RATE:
            amount = int(stake * random.uniform(*ECONOMY.HUNT_REWARD_MULT))
            await adjust_balance(session, state, amount, "hunt", ref="success", group_id=message.chat.id)
            line = react(win_category(amount), amount=amount)
            text = f"Hunt\ntarget secured.\n+{format_amount(amount)}\n{line}"
        else:
            loss = int(stake * random.uniform(*ECONOMY.HUNT_LOSS_MULT))
            await adjust_balance(session, state, -loss, "hunt", ref="failure", group_id=message.chat.id)
            line = react(loss_category(loss), amount=loss)
            text = f"{pe('skull')} Hunt failed.\n-{format_amount(loss)}\n{line}"

    await message.reply(text)


@router.message(Command("luck"))
async def luck(message: Message):
    """Gains-only -- unlike /hunt, there's no downside here at all. Three
    tiers: rare zero, common medium, rare big."""
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)

        remaining = await cd.check(session, user.id, message.chat.id, "luck")
        if remaining:
            await message.reply(f"{pe('afk')} already tried your luck today. back in {cd.format_remaining(remaining)}.")
            return

        await cd.set_cooldown(session, user.id, message.chat.id, "luck", ECONOMY.LUCK_COOLDOWN_S)
        roll = random.random()
        if roll < ECONOMY.LUCK_ZERO_RATE:
            text = f"{pe('ns')} Luck\nnothing this time. try again tomorrow."
        elif roll < ECONOMY.LUCK_ZERO_RATE + ECONOMY.LUCK_BIG_RATE:
            amount = random.randint(ECONOMY.LUCK_BIG_MIN, ECONOMY.LUCK_BIG_MAX)
            await adjust_balance(session, state, amount, "luck", ref="big win", group_id=message.chat.id)
            text = f"{pe('pog')} Luck\ntoday is your day.\n+{format_amount(amount)}"
        else:
            amount = random.randint(ECONOMY.LUCK_MEDIUM_MIN, ECONOMY.LUCK_MEDIUM_MAX)
            await adjust_balance(session, state, amount, "luck", ref="good day", group_id=message.chat.id)
            text = f"{pe('gold')} Luck\nnice pull.\n+{format_amount(amount)}"

    await message.reply(text)
            
