"""
Full vertical slice for RPS: this is the template other PvP+House games
(coin/dice/highlow) will copy. Two entry paths:

  /rps 250k            -> if no reply, creates an open PvP challenge (+ vs bot button)
  /rps 250k (in reply)  -> reserved for future direct-challenge-a-user; not required by spec

House play and PvP both go through the same response_engine + economy
primitives so personality and safety logic never diverge between modes.
"""
import json

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from app.config import ECONOMY
from app.database.db import get_session
from app.database.models import GameHistory, User
from app.games import rps as engine
from app.services.economy import (
    get_or_create_user, get_or_create_group, get_or_create_state,
    parse_amount, InvalidAmount, InsufficientBalance, available_balance,
    adjust_balance, format_amount, record_result,
)
from app.services.challenge import create_challenge, resolve_challenge, get_challenge
from app.services.response_engine import react, win_category, loss_category, wager_framing
from app.utils.keyboards import challenge_keyboard, rps_choice_keyboard
from app.utils.html_esc import esc
from app.services.premium_emoji import pe

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


async def _ensure_player(session, message_or_user, chat):
    user = message_or_user
    await get_or_create_user(session, user.id, user.full_name, user.username)
    await get_or_create_group(session, chat.id, chat.title or "")
    return await get_or_create_state(session, user.id, chat.id)


@router.message(Command("rps"))
async def rps_cmd(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("usage: /rps &lt;amount&gt;  e.g. /rps 250k")
        return
    try:
        wager = parse_amount(parts[1])
    except InvalidAmount as e:
        await message.reply(f"can't do that: {e}")
        return

    async with get_session() as session:
        state = await _ensure_player(session, message.from_user, message.chat)
        if wager > available_balance(state):
            await message.reply("you don't have that much available.")
            return
        try:
            challenge = await create_challenge(session, message.chat.id, "rps", message.from_user.id, wager)
        except InsufficientBalance:
            await message.reply("you don't have that much available.")
            return
        challenge_id = challenge.id

    house_available = not ECONOMY.RPS_MAX_HOUSE_WAGER or wager <= ECONOMY.RPS_MAX_HOUSE_WAGER
    framing = wager_framing(wager, ECONOMY.RPS_MAX_HOUSE_WAGER)
    text = (
        f"{pe('bolt')} <b>Rock Paper Scissors · {format_amount(wager)}</b>\n\n"
        f"{pe('play')} <b>{esc(message.from_user.full_name)} wants to duel</b>\n"
        f"{pe('top')} Winner takes <b>{format_amount(wager * 2)}</b>"
    )
    if framing:
        text += f"\n\n{framing}"

    await message.answer(text, reply_markup=challenge_keyboard(challenge_id, wager, house_available))


@router.callback_query(F.data.startswith("rps:"))
async def on_rps_choice(callback: CallbackQuery):
    _, challenge_id_s, choice = callback.data.split(":")
    challenge_id = int(challenge_id_s)

    async with get_session() as session:
        challenge = await get_challenge(session, challenge_id)
        if challenge is None or challenge.status not in ("pending", "accepted"):
            await callback.answer("this duel isn't active.", show_alert=True)
            return

        state = json.loads(challenge.state)

        if state.get("vs_house"):
            if callback.from_user.id != challenge.creator_id:
                await callback.answer("this isn't your game.", show_alert=True)
                return
            house_pick = engine.house_choice()
            outcome = engine.resolve(choice, house_pick)
            player_state = await get_or_create_state(session, challenge.creator_id, challenge.group_id)

            from app.services.economy import release_reservation
            await release_reservation(session, player_state, challenge.wager)

            if outcome.name == "DRAW":
                text_lines = [
                    f"{pe('bolt')} RPS",
                    f"you chose {engine.EMOJI[choice]}",
                    f"House chose {engine.EMOJI[house_pick]}",
                    "",
                    f"{pe('wp')} DRAW",
                    react("draw"),
                ]
                record_result(player_state, None)
                result = "draw"
            elif outcome.name == "WIN":
                await adjust_balance(session, player_state, challenge.wager, "game", ref=f"rps#{challenge.id} house win", group_id=challenge.group_id)
                record_result(player_state, True)
                text_lines = [
                    f"{pe('bolt')} RPS",
                    f"you chose {engine.EMOJI[choice]}",
                    f"House chose {engine.EMOJI[house_pick]}",
                    "",
                    (pe("top") + " <b>YOU WIN</b>"),
                    f"+{format_amount(challenge.wager)}",
                    react("house_win"),
                ]
                result = "win"
            else:
                await adjust_balance(session, player_state, -challenge.wager, "game", ref=f"rps#{challenge.id} house loss", group_id=challenge.group_id)
                record_result(player_state, False)
                text_lines = [
                    f"{pe('bolt')} RPS",
                    f"you chose {engine.EMOJI[choice]}",
                    f"House chose {engine.EMOJI[house_pick]}",
                    "",
                    f"{pe('skull')} <b>YOU LOSE</b>",
                    f"-{format_amount(challenge.wager)}",
                    react("house_loss"),
                ]
                result = "loss"

            challenge.status = "resolved"
            session.add(GameHistory(
                group_id=challenge.group_id, game="rps", mode="house",
                player_id=challenge.creator_id, opponent_id=None, wager=challenge.wager,
                result=result,
            ))
            await callback.message.edit_text("\n".join(text_lines))
            await callback.answer()
            return

        if callback.from_user.id not in (challenge.creator_id, challenge.acceptor_id):
            await callback.answer("this isn't your duel.", show_alert=True)
            return
        role = "creator_choice" if callback.from_user.id == challenge.creator_id else "acceptor_choice"
        if role in state:
            await callback.answer("you already chose.", show_alert=True)
            return
        state[role] = choice
        challenge.state = json.dumps(state)
        await session.flush()

        if "creator_choice" not in state or "acceptor_choice" not in state:
            await callback.answer("choice locked in.")
            return

        # both chosen -> resolve
        outcome = engine.resolve(state["creator_choice"], state["acceptor_choice"])
        creator_id, acceptor_id = challenge.creator_id, challenge.acceptor_id
        wager = challenge.wager

        winner_id = None if outcome.name == "DRAW" else (
            creator_id if outcome.value == "win" else acceptor_id
        )
        await resolve_challenge(session, challenge, winner_id)

        creator_state = await get_or_create_state(session, creator_id, challenge.group_id)
        acceptor_state = await get_or_create_state(session, acceptor_id, challenge.group_id)
        record_result(creator_state, None if winner_id is None else winner_id == creator_id)
        record_result(acceptor_state, None if winner_id is None else winner_id == acceptor_id)

        session.add(GameHistory(
            group_id=challenge.group_id, game="rps", mode="pvp",
            player_id=creator_id, opponent_id=acceptor_id, wager=wager,
            result="draw" if winner_id is None else ("win" if winner_id == creator_id else "loss"),
        ))

        creator_name = esc((await session.get(User, creator_id)).display_name)
        acceptor_name = esc((await session.get(User, acceptor_id)).display_name)
        streak_of_winner = None
        if winner_id == creator_id:
            streak_of_winner = creator_state.win_streak
        elif winner_id == acceptor_id:
            streak_of_winner = acceptor_state.win_streak

    lines = [
        f"{pe('bolt')} RPS",
        f"{creator_name} chose {engine.EMOJI[state['creator_choice']]}",
        f"{acceptor_name} chose {engine.EMOJI[state['acceptor_choice']]}",
        "",
    ]
    if winner_id is None:
        lines.append(f"{pe('wp')} DRAW")
        lines.append(react("draw"))
        lines.append(f"{format_amount(wager)} returned to both players.")
    else:
        winner_name = creator_name if winner_id == creator_id else acceptor_name
        pot = wager * 2
        lines.append(f"{pe('top')} <b>{winner_name.upper()} WINS</b>")
        lines.append(f"+{format_amount(pot - wager)}")
        if streak_of_winner and streak_of_winner >= 5:
            lines.append(react("winning_streak", streak=streak_of_winner, name=winner_name))
        else:
            lines.append(react(win_category(pot - wager), amount=pot - wager))

    await callback.message.edit_text("\n".join(lines))
    await callback.answer()
