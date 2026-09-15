"""
Wraps message.reply() so one bad/unrecognized custom emoji ID doesn't
silently eat the whole reply. Telegram rejects the ENTIRE sendMessage
call if ANY <tg-emoji emoji-id="..."> tag inside it references an ID it
doesn't recognize -- unlike an unmapped EMOJI_IDS key (which pe() already
degrades to a plain fallback char before the text ever reaches Telegram,
see premium_emoji.py), a bad ID only fails at send time, as a
TelegramBadRequest. Without this, that failure is invisible: any DB
writes the handler already made (inside its own `get_session()` block,
committed before the reply) still happened, but the player never sees a
response at all.

FALLBACKS ARE NOW DERIVED, NOT HAND-WRITTEN.

The old contract was "build the message twice, once with tags and once
without, and pass both." That drifts. It drifted in /stats: the HTML
version listed every owned gift's emoji, the hand-written plain version
said "collectibles: 3 item(s)", and because ONE unregistered emoji ID
(the hardcoded Worth-line tag) failed the send every single time, the
degraded version was the only one anyone ever saw. The gift emoji were
never actually broken -- they just never got sent.

So the fallback is now generated from the HTML itself, in two stages:

  1. Strip only the <tg-emoji> wrappers, keeping each tag's inner
     fallback character (that character is exactly what Telegram itself
     shows to non-Premium users, so this matches native degradation) and
     keeping all other markup -- <b>, <code>, <blockquote> are plain
     HTML, they never fail on a bad emoji ID.
  2. If that ALSO fails, the problem wasn't an emoji ID -- it's malformed
     markup somewhere. Strip every tag and unescape entities, then send
     that. Plain text can't be rejected for formatting.

Passing an explicit fallback still works for the cases where you want
genuinely different copy in the degraded version, but you no longer have
to, and you shouldn't by default -- a derived fallback can't go stale.
"""
import html as _html
import logging
import re

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

logger = logging.getLogger("aurabot")

# <tg-emoji emoji-id="12345">X</tg-emoji>  ->  X
_TG_EMOJI_RE = re.compile(
    r'<tg-emoji\s+emoji-id="[^"]*"\s*>(.*?)</tg-emoji>',
    re.DOTALL | re.IGNORECASE,
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_EMOJI_ID_RE = re.compile(r'<tg-emoji\s+emoji-id="([^"]*)"', re.IGNORECASE)


def strip_tg_emoji(text: str) -> str:
    """Replaces every custom-emoji tag with its own fallback character,
    leaving all other HTML intact. This is the stage-1 fallback: the
    message keeps 100% of its content and formatting, and only loses the
    premium glyphs."""
    return _TG_EMOJI_RE.sub(lambda m: m.group(1), text)


def strip_all_html(text: str) -> str:
    """Stage-2 fallback: no markup at all. Entities are unescaped so
    text that went through esc() doesn't surface as raw &amp;lt; once the
    tags around it are gone."""
    return _html.unescape(_ANY_TAG_RE.sub("", text))


def _log_rejection(html_text: str) -> None:
    """Telegram's error text says a message was rejected but never says
    WHICH emoji ID it choked on, so a bad ID is otherwise invisible --
    you only see the degraded message in chat and have to guess. Logging
    every ID in the rejected send narrows it to a short list you can
    check one at a time with /emojiid, which is the only reliable way to
    tell a good ID from a dead one."""
    ids = _EMOJI_ID_RE.findall(html_text)
    logger.warning(
        "reply rejected -- degrading. custom emoji IDs in this message: %s",
        ", ".join(ids) or "(none)",
        exc_info=True,
    )


async def safe_reply(message: Message, html_text: str, plain_fallback: str | None = None, **kwargs) -> None:
    """Sends html_text. On a formatting/emoji rejection, degrades instead
    of vanishing. `plain_fallback` is optional -- omit it unless the
    degraded message genuinely needs different wording, and let the
    derived version handle the rest."""
    try:
        await message.reply(html_text, **kwargs)
        return
    except TelegramBadRequest:
        _log_rejection(html_text)

    stage_one = plain_fallback if plain_fallback is not None else strip_tg_emoji(html_text)
    try:
        await message.reply(stage_one, **kwargs)
        return
    except TelegramBadRequest:
        logger.warning("fallback reply also rejected -- retrying as bare text", exc_info=True)

    await message.reply(strip_all_html(stage_one), **kwargs)


async def safe_answer(message: Message, html_text: str, plain_fallback: str | None = None, **kwargs) -> None:
    """Same ladder as safe_reply, but sends into the chat instead of
    replying to a specific message. Useful where the triggering message
    may already be gone (callback flows)."""
    try:
        await message.answer(html_text, **kwargs)
        return
    except TelegramBadRequest:
        _log_rejection(html_text)

    stage_one = plain_fallback if plain_fallback is not None else strip_tg_emoji(html_text)
    try:
        await message.answer(stage_one, **kwargs)
        return
    except TelegramBadRequest:
        logger.warning("fallback answer also rejected -- retrying as bare text", exc_info=True)

    await message.answer(strip_all_html(stage_one), **kwargs)
