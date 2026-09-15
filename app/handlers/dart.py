"""
/dart <amount> -- solo vs-house, one throw, no hosting/accepting needed.
Uses Telegram's real animated dart emoji so it actually looks like a dart
being thrown instead of the bot just declaring a result.

Originally this asked players to call "white" or "red" -- scrapped after
testing showed Telegram's dart animation always renders the SAME red/white
ringed board no matter what, so there was never an actual color outcome to
guess in the first place (see conversation: a throw visibly hit red, bot
said "white"). Rebuilt around what Telegram actually gives us: value 1 =
miss, value 6 = bullseye, 2-5 = a hit somewhere on the board (documented
behavior, though officially "undocumented and might change" per Telegram).

No guessing at all now -- the throw itself decides the outcome. Odds are
tuned to zero expected value (same discipline as every other game here):
  - value 1 (miss, 1/6): push, wager returned
  - values 2,3,4 (lose, 3/6): lose the wager
  - value 5 (win, 1/6): double up (+1x profit)
  - value 6 (bullseye, 1/6): +2x profit
EV = (-3 + 1 + 2) / 6 = 0 -- fair, not a repeatable money printer like the
old /hunt bug or the original color-guess version of this game.
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from app.config import ECONOMY
from app.database.db import get_session
from app.services.economy import (
    get_or_create_user, get_or_create_group, get_or_create_state,
    parse_amount, InvalidAmount, available_balance, format_amount, adjust_balance,
)
from app.services.game_common import finalize_house
from app.services.response_engine import react
from app.services.premium_emoji import pe
from app.utils.html_esc import esc

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(Command("dart"))
async def dart_cmd(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("usage: /dart &lt;amount&gt;  e.g. /dart 100k")
        return
    try:
        wager = parse_amount(parts[1])
    except InvalidAmount as e:
        await message.reply(f"can't do that: {e}")
        return

    if wager > ECONOMY.DART_MAX_WAGER:
        await message.reply(f"max dart wager is {format_amount(ECONOMY.DART_MAX_WAGER)}.")
        return

    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)
        if wager > available_balance(state):
            await message.reply("you don't have that much available.")
            return

    throw_msg = await message.answer_dice(emoji="🎯")
    value = throw_msg.dice.value

    lines = [f"{pe('dart')} <b>Dart · {esc(user.full_name)}</b>", ""]

    async with get_session() as session:
        state = await get_or_create_state(session, user.id, message.chat.id)

        if value == 1:
            lines.append(f"{pe('wp')} missed the board entirely. wager returned.")
            lines.append(react("draw"))
            await finalize_house(session, "dart", state, message.chat.id, wager, None)
        elif value == 6:
            lines.append(f"{pe('vip')} <b>BULLSEYE</b>")
            lines.append(f"+{format_amount(wager * 2)}")
            lines.append(react("house_win"))
            # finalize_house only moves the wager 1x on a win, so the extra
            # 1x for the bullseye bonus is applied as a separate adjustment
            await finalize_house(session, "dart", state, message.chat.id, wager, True)
            await adjust_balance(session, state, wager, "game", ref="dart bullseye bonus", group_id=message.chat.id)
        elif value == 5:
            lines.append("hit the board. solid throw.")
            lines.append(f"{pe('top')} <b>YOU WIN</b>")
            lines.append(f"+{format_amount(wager)}")
            lines.append(react("house_win"))
            await finalize_house(session, "dart", state, message.chat.id, wager, True)
        else:
            lines.append("hit the board, but not enough.")
            lines.append(f"{pe('skull')} <b>YOU LOSE</b>")
            lines.append(f"-{format_amount(wager)}")
            lines.append(react("house_loss"))
            await finalize_house(session, "dart", state, message.chat.id, wager, False)

    await message.answer("\n".join(lines))
  
