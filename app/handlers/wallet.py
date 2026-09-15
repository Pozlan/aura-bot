from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select, desc

from app.database.db import get_session
from app.database.models import PlayerState, User, Transaction, Gift
from app.services.economy import (
    get_or_create_user, get_or_create_group, get_or_create_state, format_amount,
    available_balance, GLOBAL_ID,
)
from app.services.gifts import badge_tag, player_cabinet
from app.services.premium_emoji import pe, raw_tag
from app.utils.cabinet import render_cabinet
from app.utils.html_esc import esc
from app.utils.safe_reply import safe_reply

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(Command("start"))
async def start(message: Message):
    """Kept short and formal on purpose -- the DM /start (inbox.py) carries
    the full welcome copy, banner, and AURA mark; this one just confirms the
    bot's alive in the group and points at /help."""
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        await get_or_create_state(session, user.id, message.chat.id)
    await message.reply(f"{pe('play')} <b>aura</b> is live in this chat. use <code>/help</code> to see the commands.")


@router.message(Command("help"))
async def help_cmd(message: Message):
    # <blockquote expandable> is real Telegram HTML (needs ParseMode.HTML,
    # already the bot default) -- renders collapsed with a tap-to-expand
    # affordance, same UI as manually quoting + collapsing text in the
    # client. Tagline stays outside it so there's still something to read
    # before anyone expands the full command list.
    await message.reply(
        f"{pe('play')} <b>aura</b>\n\n"
        "mini-games, gambling, tips, and flex for the group. fast rounds, real payouts.\n\n"
        "<blockquote expandable>"
        "<code>/farm</code> - daily claim\n"
        "<code>/work</code> - take a job\n"
        "<code>/loot</code> - chance find\n"
        "<code>/hunt &lt;amount&gt;</code> - risk it for more\n"
        "<code>/luck</code> - daily gamble\n\n"
        "<code>/rps &lt;amount&gt;</code> - rock paper scissors\n"
        "<code>/coin &lt;amount&gt;</code> - coin flip\n"
        "<code>/dice &lt;amount&gt;</code> - dice duel\n"
        "<code>/highlow &lt;amount&gt;</code> - guess the next card\n"
        "<code>/dart &lt;amount&gt;</code> - throw a dart\n"
        "<code>/cancel</code> - refund your own unaccepted game\n\n"
        "<code>/tip &lt;amount&gt;</code> - reply to send aura\n"
        "<code>/rob</code> - reply to try a robbery\n"
        "<code>/protect</code> - 24h robbery shield\n\n"
        "<code>/shop</code> - spend aura on gifts\n"
        "<code>/sellback</code> - sell a gift back\n"
        "<code>/equip</code> - show a gift badge\n\n"
        "<code>/bal</code> - your balance\n"
        "<code>/stats</code> - your record\n"
        "<code>/top</code> - leaderboard here\n"
        "<code>/gtop</code> - leaderboard everywhere"
        "</blockquote>\n\n"
        "DM me <code>/bal</code>, <code>/stats</code>, or <code>/gtop</code> any time to check in privately."
    )


@router.message(Command("bal"))
async def bal(message: Message):
    """Balance is global (see economy.GLOBAL_ID) — same number in every
    group. Also surfaces anything currently locked in an open challenge you
    hosted, so a stuck reservation is never invisible again."""
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)
        badge = await badge_tag(session, state)

    lines = [f"<b>{esc(user.full_name)}</b>{badge}", format_amount(state.balance)]
    if state.reserved > 0:
        lines.append(f"{pe('afk')} {format_amount(state.reserved)} locked in an open challenge")
        lines.append(f"available: {format_amount(available_balance(state))}")
    await message.reply("\n".join(lines))


@router.message(Command("top"))
async def top(message: Message):
    """Group-local leaderboard. Balances are global (see economy.GLOBAL_ID),
    so 'local to this group' means: rank by global balance, but only
    include players with actual transaction history in THIS group -- pulled
    from the ledger, which still records the real group_id per action even
    though the wallet itself isn't split per group anymore."""
    async with get_session() as session:
        group_member_ids = select(Transaction.user_id).where(Transaction.group_id == message.chat.id).distinct()
        stmt = (
            select(PlayerState, User, Gift.emoji_id)
            .join(User, User.id == PlayerState.user_id)
            .outerjoin(Gift, Gift.id == PlayerState.equipped_gift_id)
            .where(PlayerState.group_id == GLOBAL_ID, PlayerState.user_id.in_(group_member_ids))
            .order_by(desc(PlayerState.balance))
            .limit(10)
        )
        rows = (await session.execute(stmt)).all()

    if not rows:
        await message.reply(f"{pe('top')} nobody's played in this group yet.")
        return

    lines = [f"{pe('top')} <b>LEADERBOARD</b>", ""]
    for i, (state, player, badge_id) in enumerate(rows, start=1):
        badge = f" {raw_tag(badge_id)}" if badge_id else ""
        lines.append(f"{i}. {esc(player.display_name)}{badge} · {format_amount(state.balance)}")
    lines.append("")
    lines.append("this group only. <code>/gtop</code> for everyone, everywhere.")
    await message.reply("\n".join(lines))


@router.message(Command("gtop"))
async def gtop(message: Message):
    """True global top 10, every player, every group."""
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
        await message.reply(f"{pe('top')} nobody's on the board yet. play something first.")
        return

    lines = [f"{pe('top')} <b>AURA GLOBAL LEADERBOARD</b>", ""]
    for i, (state, player, badge_id) in enumerate(rows, start=1):
        badge = f" {raw_tag(badge_id)}" if badge_id else ""
        lines.append(f"{i}. {esc(player.display_name)}{badge} · {format_amount(state.balance)}")
    await message.reply("\n".join(lines))


@router.message(Command("stats"))
async def stats(message: Message):
    """Pure flex screen -- no W/L, no win rate, no rank, no wager (that's
    /bal's job). Name, equipped badge, balance, gift cabinet.

    Now byte-for-byte the same output the DM version produces (shared
    renderer, utils/cabinet.py). It previously carried an extra streak
    line the DM version didn't have, and that line was the only place the
    two differed -- which makes it the only candidate for why this one
    kept degrading to plain text while the DM one rendered correctly.
    The streak count lives in /streak, where it's shown in digit glyphs.
    """
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)
        badge = await badge_tag(session, state)
        cabinet = await player_cabinet(session, user.id)
        balance = state.balance

    lines = [f"<b>{esc(user.full_name)}</b>{badge}", format_amount(balance), ""]
    lines += render_cabinet(cabinet, "empty. check /shop and start flexing.")

    # safe_reply derives its own degraded version by stripping the emoji
    # tags, so nothing is lost if Telegram rejects one -- and it logs
    # every emoji ID in the rejected message, which is how a bad ID gets
    # identified instead of guessed at.
    await safe_reply(message, "\n".join(lines))
