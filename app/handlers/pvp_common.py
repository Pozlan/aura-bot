"""
Every game's "Accept" and "Play vs Bot" buttons post here -- one Router,
one place that knows how to dispatch by challenge.game. HighLow isn't
handled here anymore -- it's a solo streak game against the house with
its own run lifecycle (see highlow_service.py), not a paired challenge.

RPS needs an extra step (both players privately choose) before it can
resolve, so it hands off to rps.py's own "rps:" callback. Coin needs one
pick vs house (heads/tails). Dice needs none -- PvP and House dice both
use Telegram's native animated dice (bot.send_dice) instead of a plain
random number, so it actually LOOKS like a dice roll instead of the bot
just declaring a winner.
"""
import json

from aiogram import Router, F
from aiogram.types import CallbackQuery

from app.config import ECONOMY
from app.database.db import get_session
from app.games import coin as coin_engine, dice as dice_engine
from app.services.challenge import accept_challenge, get_challenge, cancel_own_pending, ChallengeError
from app.services.economy import (
    get_or_create_user, get_or_create_group, get_or_create_state,
    format_amount, release_reservation,
)
from app.services.game_common import finalize_pvp, finalize_house
from app.services.response_engine import react, win_category
from app.services.premium_emoji import pe
from app.utils.keyboards import rps_choice_keyboard, coin_choice_keyboard
from aiogram.filters import Command
from aiogram.types import Message

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

HOUSE_CAP = {
    "coin": ECONOMY.COIN_MAX_HOUSE_WAGER,
    "dice": ECONOMY.DICE_MAX_HOUSE_WAGER,
}
GAME_EMOJI = {"coin": "🪙", "dice": "🎲"}
GAME_TITLE = {"coin": "Coin Flip", "dice": "Dice Duel"}


def _game_header(game: str, suffix: str = "") -> str:
    return f"{GAME_EMOJI[game]} {GAME_TITLE[game]}{suffix}"


async def _ensure_player(session, user, chat):
    await get_or_create_user(session, user.id, user.full_name, user.username)
    await get_or_create_group(session, chat.id, chat.title or "")
    return await get_or_create_state(session, user.id, chat.id)


@router.message(Command("cancel"))
async def cancel_cmd(message: Message):
    """Public self-service cancel -- anyone can bail on their OWN hosted
    game while it's still unaccepted, instant refund, no waiting for the
    3-min timer. Deliberately can't touch a game someone already accepted
    (see cancel_own_pending) -- that's the line that keeps this safe to
    make public instead of owner-only like /reset."""
    async with get_session() as session:
        cancelled = await cancel_own_pending(session, message.from_user.id)

    if not cancelled:
        await message.reply("you don't have any open (unaccepted) games to cancel.")
        return

    total = sum(c.wager for c in cancelled)
    await message.reply(f"cancelled {len(cancelled)} game(s), {format_amount(total)} refunded.")


@router.callback_query(F.data.startswith("acc:"))
async def on_accept(callback: CallbackQuery):
    challenge_id = int(callback.data.split(":")[1])
    async with get_session() as session:
        await _ensure_player(session, callback.from_user, callback.message.chat)
        try:
            challenge = await accept_challenge(session, challenge_id, callback.from_user.id)
        except ChallengeError as e:
            await callback.answer(str(e), show_alert=True)
            return
        game = challenge.game
        creator_id, acceptor_id, wager = challenge.creator_id, challenge.acceptor_id, challenge.wager

    if game == "rps":
        await callback.message.edit_text(
            f"{pe('crossed_swords')} <b>RPS DUEL</b>\n\nchoose your weapon.", reply_markup=rps_choice_keyboard(challenge_id)
        )
        await callback.answer()
        return

    if game == "dice":
        # real animated dice, not a silently-picked number -- this is what
        # was fixed after feedback that dice "felt fake"
        await callback.message.edit_text(f"🎲 <b>Dice Duel · {format_amount(wager)}</b>\n\nrolling...")
        roll_a_msg = await callback.message.answer_dice(emoji="🎲")
        roll_b_msg = await callback.message.answer_dice(emoji="🎲")
        roll_a, roll_b = roll_a_msg.dice.value, roll_b_msg.dice.value
        outcome = dice_engine.resolve(roll_a, roll_b)
        winner_id = None if outcome.name == "DRAW" else (
            creator_id if outcome.name == "WIN" else acceptor_id
        )
        summary = f"🎲 {roll_a} vs 🎲 {roll_b}"
    elif game == "coin":
        result = coin_engine.flip()
        winner_id = creator_id if result == "heads" else acceptor_id
        summary = f"the coin landed on <b>{result.upper()}</b>"
    else:
        await callback.answer("unsupported game.", show_alert=True)
        return

    async with get_session() as session:
        challenge = await get_challenge(session, challenge_id)
        info = await finalize_pvp(session, challenge, winner_id)

    lines = [
        _game_header(game),
        summary,
        f"{info['creator_name']} vs {info['acceptor_name']}",
        "",
    ]
    if winner_id is None:
        lines.append(f"{pe('wp')} DRAW")
        lines.append(react("draw"))
        lines.append(f"{format_amount(info['wager'])} returned to both players.")
    else:
        winner_name = info["creator_name"] if winner_id == info["creator_id"] else info["acceptor_name"]
        winner_state = info["creator_state"] if winner_id == info["creator_id"] else info["acceptor_state"]
        payout = info["wager"]
        lines.append(f"{pe('top')} <b>{winner_name.upper()} WINS</b>")
        lines.append(f"+{format_amount(payout)}")
        if winner_state.win_streak >= 5:
            lines.append(react("winning_streak", streak=winner_state.win_streak, name=winner_name))
        else:
            lines.append(react(win_category(payout), amount=payout))

    if game == "dice":
        await callback.message.answer("\n".join(lines))
    else:
        await callback.message.edit_text("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data.startswith("vsbot:"))
async def on_vs_bot(callback: CallbackQuery):
    challenge_id = int(callback.data.split(":")[1])
    async with get_session() as session:
        challenge = await get_challenge(session, challenge_id)
        if challenge is None or challenge.status != "pending":
            await callback.answer("this challenge isn't available anymore.", show_alert=True)
            return
        if callback.from_user.id != challenge.creator_id:
            await callback.answer("only the creator can play this vs the bot.", show_alert=True)
            return
        cap = HOUSE_CAP.get(challenge.game)
        if cap and challenge.wager > cap:
            await callback.answer("wager too high to play vs the bot.", show_alert=True)
            return
        game = challenge.game
        wager = challenge.wager

        if game == "rps":
            state = json.loads(challenge.state)
            state["vs_house"] = True
            challenge.state = json.dumps(state)
            await session.flush()
            await callback.message.edit_text(
                f"{pe('play')} RPS vs House\n\nchoose your weapon.", reply_markup=rps_choice_keyboard(challenge_id)
            )
            await callback.answer()
            return

        if game == "coin":
            await callback.message.edit_text(
                _game_header(game, " vs House") + "\n\ncall it.",
                reply_markup=coin_choice_keyboard(challenge_id),
            )
            await callback.answer()
            return

        if game != "dice":
            await callback.answer("unsupported game.", show_alert=True)
            return

    # dice vs house: real animated rolls, done outside the DB session since
    # it's pure Telegram I/O -- then a second session finalizes the payout
    await callback.message.edit_text(f"🎲 <b>Dice Duel vs House · {format_amount(wager)}</b>\n\nrolling...")
    player_roll_msg = await callback.message.answer_dice(emoji="🎲")
    house_roll_msg = await callback.message.answer_dice(emoji="🎲")
    player_roll, house_roll = player_roll_msg.dice.value, house_roll_msg.dice.value
    outcome = dice_engine.resolve(player_roll, house_roll)
    won = None if outcome.name == "DRAW" else (outcome.name == "WIN")
    summary = f"you rolled 🎲 <b>{player_roll}</b>, house rolled 🎲 <b>{house_roll}</b>"

    async with get_session() as session:
        challenge = await get_challenge(session, challenge_id)
        if challenge is None or challenge.status != "pending":
            await callback.answer("this challenge isn't available anymore.", show_alert=True)
            return
        player_state = await get_or_create_state(session, challenge.creator_id, challenge.group_id)
        await release_reservation(session, player_state, challenge.wager)
        await finalize_house(session, "dice", player_state, challenge.group_id, challenge.wager, won)
        challenge.status = "resolved"

    lines = [_game_header(game, " vs House"), summary, ""]
    if won is None:
        lines.append(f"{pe('wp')} DRAW. wager returned.")
        lines.append(react("draw"))
    elif won:
        lines.append(f"{pe('top')} <b>YOU WIN</b>")
        lines.append(f"+{format_amount(wager)}")
        lines.append(react("house_win"))
    else:
        lines.append(f"{pe('skull')} <b>YOU LOSE</b>")
        lines.append(f"-{format_amount(wager)}")
        lines.append(react("house_loss"))

    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data.startswith("coin:"))
async def on_coin_call(callback: CallbackQuery):
    _, challenge_id_s, call = callback.data.split(":")
    challenge_id = int(challenge_id_s)

    async with get_session() as session:
        challenge = await get_challenge(session, challenge_id)
        if challenge is None or challenge.status != "pending":
            await callback.answer("this game isn't active.", show_alert=True)
            return
        if callback.from_user.id != challenge.creator_id:
            await callback.answer("this isn't your game.", show_alert=True)
            return

        result = coin_engine.flip()
        won = result == call
        player_state = await get_or_create_state(session, challenge.creator_id, challenge.group_id)
        await release_reservation(session, player_state, challenge.wager)
        await finalize_house(session, "coin", player_state, challenge.group_id, challenge.wager, won)
        challenge.status = "resolved"
        wager = challenge.wager

    lines = [
        _game_header("coin", " vs House"),
        f"you called <b>{call}</b>",
        f"landed on <b>{result}</b>",
        "",
    ]
    if won:
        lines.append(f"{pe('top')} <b>YOU WIN</b>")
        lines.append(f"+{format_amount(wager)}")
        lines.append(react("house_win"))
    else:
        lines.append(f"{pe('skull')} <b>YOU LOSE</b>")
        lines.append(f"-{format_amount(wager)}")
        lines.append(react("house_loss"))

    await callback.message.edit_text("\n".join(lines))
    await callback.answer()
                                  
