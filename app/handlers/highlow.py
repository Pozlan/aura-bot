from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import ECONOMY
from app.database.db import get_session
from app.database.models import HighLowRun
from app.games import highlow as engine
from app.services.economy import (
    get_or_create_user, get_or_create_group, get_or_create_state,
    parse_amount, InvalidAmount, InsufficientBalance, available_balance, format_amount,
)
from app.services.highlow_service import start_run, resolve_guess, HighLowError
from app.services.response_engine import react, win_category, loss_category
from app.services.premium_emoji import pe
from app.utils.html_esc import esc

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


def _keyboard(run_id: int, card: int) -> InlineKeyboardMarkup:
    row = []
    if engine.can_guess_higher(card):
        row.append(InlineKeyboardButton(text="🟩 Higher", callback_data=f"hl:{run_id}:higher"))
    if engine.can_guess_lower(card):
        row.append(InlineKeyboardButton(text="🟥 Lower", callback_data=f"hl:{run_id}:lower"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


@router.message(Command("highlow"))
async def highlow_cmd(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("usage: /highlow &lt;amount&gt;  e.g. /highlow 200k")
        return
    try:
        wager = parse_amount(parts[1])
    except InvalidAmount as e:
        await message.reply(f"can't do that: {e}")
        return
    if ECONOMY.HIGHLOW_MAX_HOUSE_WAGER and wager > ECONOMY.HIGHLOW_MAX_HOUSE_WAGER:
        await message.reply(f"max wager for /highlow is {format_amount(ECONOMY.HIGHLOW_MAX_HOUSE_WAGER)}.")
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
            run = await start_run(session, user.id, message.chat.id, wager)
        except InsufficientBalance:
            await message.reply("you don't have that much available.")
            return
        run_id, card = run.id, run.current_card

    text = (
        f"<tg-emoji emoji-id=\"5404556879852484450\">🃏</tg-emoji> <b>Higher / Lower · {format_amount(wager)}</b>\n\n"
        f"Card: <b>{card}</b> of 13\n\n"
        f"{esc(message.from_user.full_name)}, will the next card be higher or lower?"
    )
    await message.answer(text, reply_markup=_keyboard(run_id, card))


@router.callback_query(F.data.startswith("hl:"))
async def on_highlow_action(callback: CallbackQuery):
    _, run_id_s, action = callback.data.split(":")
    run_id = int(run_id_s)

    async with get_session() as session:
        run = await session.get(HighLowRun, run_id)
        if run is None:
            await callback.answer("this game no longer exists.", show_alert=True)
            return
        if callback.from_user.id != run.user_id:
            await callback.answer("this isn't your game.", show_alert=True)
            return
        if run.status != "active":
            await callback.answer("this game already ended.", show_alert=True)
            return

        try:
            result = await resolve_guess(session, run, action)
        except HighLowError as e:
            await callback.answer(str(e), show_alert=True)
            return

        wager = run.wager

    first_card, second_card = result["first_card"], result["second_card"]

    if result["won"]:
        net = result["net"]
        text = (
            "<tg-emoji emoji-id=\"5404556879852484450\">🃏</tg-emoji> Higher / Lower\n\n"
            f"Card was <b>{first_card}</b>, next was <b>{second_card}</b>. correct!\n\n"
            f"{pe('top')} <b>YOU WIN</b>\n"
            f"+{format_amount(net)}\n"
            f"{react(win_category(net), amount=net)}"
        )
    else:
        text = (
            "🃏 Higher / Lower\n\n"
            f"Card was <b>{first_card}</b>, next was <b>{second_card}</b>\n\n"
            f"{pe('skull')} <b>YOU LOSE</b>\n"
            f"-{format_amount(wager)}\n"
            f"{react(loss_category(wager), amount=wager)}"
        )

    await callback.message.edit_text(text)
    await callback.answer()

"<tg-emoji emoji-id=\"5404556879852484450\">🃏</tg-emoji> Higher / Lower\n\n"
