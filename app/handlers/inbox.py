"""
DM (private chat) support. Every other router in this project filters to
group/supergroup only, so before this file existed the bot did nothing at
all in a private chat -- not even /start replied.

Scope on purpose: DMs are for checking in (/bal, /stats, /gtop), not for
playing. Games, farming, tipping, robbing etc. all stay group-only -- that's
social by design. Anything else typed here gets a short redirect instead of
being silently ignored.
"""
from pathlib import Path

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, desc

from app.database.db import get_session
from app.database.models import PlayerState, User, Gift
from app.services.economy import get_or_create_user, get_or_create_state, format_amount, available_balance, GLOBAL_ID
from app.services.gifts import badge_tag, player_cabinet
from app.services.premium_emoji import pe, raw_tag
from app.utils.cabinet import render_cabinet
from app.utils.html_esc import esc
from app.utils.safe_reply import safe_reply

router = Router()
router.message.filter(F.chat.type == "private")

BANNER_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "aura_banner.png"


@router.message(Command("start"))
async def start_dm(message: Message, bot: Bot):
    user = message.from_user
    async with get_session() as session:
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_state(session, user.id)

    caption = (
        f"Welcome to Aura{pe('logo')}\n\n"
        "Play games, challenge your friends and earn Aura\n\n"
        "Think you've got what it takes huh?\n"
        "/help to see the games.\n\n"
        f"{pe('play')} no group yet? join @PozzVerse to play with others."
    )
    # startgroup=true is the magic param -- tapping this opens Telegram's
    # own group picker and adds the bot directly, no manual @username
    # search needed. bot.get_me() is cached by aiogram after the first
    # call, so this isn't a fresh API hit on every /start.
    me = await bot.get_me()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Add to Group", url=f"https://t.me/{me.username}?startgroup=true")
    ]])
    if BANNER_PATH.exists():
        await message.answer_photo(FSInputFile(BANNER_PATH), caption=caption, reply_markup=kb)
    else:
        await message.reply(caption, reply_markup=kb)


@router.message(Command("bal"))
async def bal_dm(message: Message):
    user = message.from_user
    async with get_session() as session:
        await get_or_create_user(session, user.id, user.full_name, user.username)
        state = await get_or_create_state(session, user.id)
        badge = await badge_tag(session, state)

    lines = [f"<b>{esc(user.full_name)}</b>{badge}", format_amount(state.balance)]
    if state.reserved > 0:
        lines.append(f"{pe('afk')} {format_amount(state.reserved)} locked in an open challenge")
        lines.append(f"available: {format_amount(available_balance(state))}")
    await message.reply("\n".join(lines))


@router.message(Command("stats"))
async def stats_dm(message: Message):
    """Unchanged in output -- this is the version that already rendered
    correctly, so the layout is preserved exactly and handlers/wallet.py
    was brought in line with it instead of the other way round. The
    cabinet block now comes from the shared renderer (utils/cabinet.py)
    so the group and DM versions can't drift apart again.

    The one behavioural change: it sends through safe_reply now. A bare
    message.reply() meant that if any emoji ID in here ever did get
    rejected, the DM replied with nothing at all -- no error, no text,
    just silence.
    """
    user = message.from_user
    async with get_session() as session:
        await get_or_create_user(session, user.id, user.full_name, user.username)
        state = await get_or_create_state(session, user.id)
        badge = await badge_tag(session, state)
        cabinet = await player_cabinet(session, user.id)
        balance = state.balance

    lines = [f"<b>{esc(user.full_name)}</b>{badge}", format_amount(balance), ""]
    lines += render_cabinet(cabinet, "empty. check /shop in a group and start flexing.")

    await safe_reply(message, "\n".join(lines))


@router.message(Command("gtop"))
async def gtop_dm(message: Message):
    async with get_session() as session:
        stmt = (
            select(PlayerState, User, Gift.emoji_id)
            .join(User, User.id == PlayerState.user_id)
            .outerjoin(Gift, Gift.id == PlayerState.equipped_gift_id)
            .where(PlayerState.group_id == GLOBAL_ID)
            .order_by(desc(PlayerState.balance))
            .limit(10)
        )
        rows = (await session.execute(stmt)).all()

    if not rows:
        await message.reply(f"{pe('top')} nobody's on the board yet.")
        return

    lines = [f"{pe('top')} <b>AURA GLOBAL LEADERBOARD</b>", ""]
    for i, (state, player, badge_id) in enumerate(rows, start=1):
        badge = f" {raw_tag(badge_id)}" if badge_id else ""
        lines.append(f"{i}. {esc(player.display_name)}{badge} · {format_amount(state.balance)}")
    await message.reply("\n".join(lines))


@router.message(Command("top"))
async def top_dm(message: Message):
    """/top is group-local (see handlers/wallet.py::top), which means
    nothing in DM. Redirect to /gtop instead of silently no-op'ing."""
    await message.reply(f"{pe('top')} <code>/top</code> is per-group, use <code>/gtop</code> here instead.")


@router.message(Command("help"))
async def help_dm(message: Message):
    await message.reply(
        f"{pe('play')} in DM you can check <code>/bal</code>, <code>/stats</code>, "
        "and <code>/gtop</code>, that's it here.\n"
        "want to actually play? join @PozzVerse."
    )


@router.message()
async def fallback_dm(message: Message):
    """Anything else typed in DM -- another command, or just a message.
    Catches games/farm/tip/rob/etc. attempts specifically since those only
    exist as group-only routers and would otherwise be silently ignored."""
    await message.reply(
        f"{pe('afk')} that one's group-only. join @PozzVerse to play.\n"
        "in here you can check <code>/bal</code>, <code>/stats</code>, and <code>/gtop</code>."
    )
