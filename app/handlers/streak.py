"""
/streak responses.

The streak COUNT -- current streak and best run -- always renders through
premium_emoji.render_streak_count(), never as a plain digit. That
function uses the ten dedicated glyphs (one per value 1-10, not spelled
out digit-by-digit) for anything in that range, which covers the
overwhelming majority of real play.

Everything else in these messages (the cooldown time, "24h") stays plain
text on purpose. The earlier version ran every number in the message
through a digit-glyph substitution, which meant every /streak send
carried a pile of custom-emoji tags -- and if Telegram rejects a message
because ANY tag's ID is bad, the whole thing degrades, fallback
characters and all (a bad tag's required fallback for a digit glyph IS a
plain digit -- that's not a bug in the fallback, that's Telegram's own
rule for what a custom emoji must degrade to). Fewer tags per message
means fewer chances for one bad ID to take the whole thing down, and this
exact message has reportedly been failing since before this file was
touched at all -- narrowing the tag surface is how to isolate it.

Icons here are limited to afk/gg/sad, the three this handler already
used before any of these changes -- proven to already work in this exact
file, unlike icons introduced later that have never been confirmed here.

If a send still degrades after this, safe_reply now logs every custom
emoji ID present in the rejected message (see utils/safe_reply.py) --
check the Render logs for a "reply rejected" line and cross-check the IDs
listed against /emojiid on the actual custom emoji in the source pack.
That will say definitively whether a digit ID is the one Telegram is
rejecting.
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from app.services import cooldown as cd
from app.database.db import get_session
from app.services.economy import get_or_create_user, get_or_create_group, get_or_create_state
from app.services.premium_emoji import pe, raw_tag, render_streak_count
from app.services.streak import activate
from app.utils.html_esc import esc
from app.utils.safe_reply import safe_reply

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(Command("streak"))
async def streak(message: Message):
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        await get_or_create_group(session, message.chat.id, message.chat.title or "")
        state = await get_or_create_state(session, user.id, message.chat.id)

        result = await activate(session, state)
        streak_best = state.streak_best

    streak_count = result.streak_count

    if not result.activated:
        remaining = cd.format_remaining(result.remaining)
        html_lines = [
            f"{pe('afk')} streak running: {render_streak_count(streak_count)}",
            "",
            f"already activated. back in {remaining} or it breaks.",
        ]
        if streak_best > streak_count:
            html_lines.append(f"best run: {render_streak_count(streak_best)}")
        await safe_reply(message, "\n".join(html_lines))
        return

    milestone_hit = result.milestone_hit
    milestone_gift = result.milestone_gift
    broken = result.broken

    html_lines = [f"{pe('gg')} streak activated: {render_streak_count(streak_count)}"]

    if broken:
        html_lines += ["", f"{pe('sad')} you missed the window — streak restarted from {render_streak_count(1)}."]

    if milestone_hit:
        html_lines += [
            "",
            f"{raw_tag(milestone_gift.emoji_id)} <b>{milestone_hit}-day milestone!</b>",
            f"{esc(user.full_name)} just earned an exclusive badge. check /stats.",
        ]
    else:
        html_lines.append("come back within 24h or it resets.")

    if streak_best > streak_count:
        html_lines.append(f"best run: {render_streak_count(streak_best)}")

    await safe_reply(message, "\n".join(html_lines))
