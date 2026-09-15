from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from app.config import ECONOMY
from app.database.db import get_session
from app.services.economy import (
    get_or_create_user, get_or_create_group, get_or_create_state,
    parse_amount, InvalidAmount, InsufficientBalance, available_balance, format_amount,
)
from app.services.challenge import create_challenge
from app.services.response_engine import wager_framing
from app.utils.keyboards import challenge_keyboard
from app.utils.html_esc import esc
from app.services.premium_emoji import pe

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(Command("coin"))
async def coin_cmd(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("usage: /coin &lt;amount&gt;  e.g. /coin 250k")
        return
    try:
        wager = parse_amount(parts[1])
    except InvalidAmount as e:
        await message.reply(f"can't do that: {e}")
        return

    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)
        if wager > available_balance(state):
            await message.reply("you don't have that much available.")
            return
        try:
            challenge = await create_challenge(session, message.chat.id, "coin", user.id, wager)
        except InsufficientBalance:
            await message.reply("you don't have that much available.")
            return
        challenge_id = challenge.id

    house_available = not ECONOMY.COIN_MAX_HOUSE_WAGER or wager <= ECONOMY.COIN_MAX_HOUSE_WAGER
    framing = wager_framing(wager, ECONOMY.COIN_MAX_HOUSE_WAGER)
    text = (
        f"🪙 <b>Coin Flip · {format_amount(wager)}</b>\n\n"
        f"{pe('play')} <b>{esc(message.from_user.full_name)} wants to flip</b>\n"
        f"{pe('top')} Winner takes <b>{format_amount(wager * 2)}</b>"
    )
    if framing:
        text += f"\n\n{framing}"

    await message.answer(text, reply_markup=challenge_keyboard(challenge_id, wager, house_available))
