"""
Telegram's "premium emoji" are called custom emoji in the Bot API. As of
the Feb 2026 Bot API update, any bot can send them for free as long as
the bot owner's account has Telegram Premium -- no NFT username required.

The one thing you can't skip: every custom emoji has a numeric ID, and
there's no way to look it up except by pulling it out of a real message
that contains it. `/emojiid` (see handlers/admin.py) does that extraction.

To actually SEND one, use aiogram's formatting module (CustomEmoji, Bold,
Text, ...) instead of hand-rolling entities -- it computes UTF-16 offsets
correctly and lets you mix custom emoji with bold/italic in one message.
`emoji_text()` below is a thin wrapper for the common case.
"""
from aiogram.types import Message, MessageEntity
from aiogram.utils.formatting import CustomEmoji


def extract_custom_emoji_ids(message: Message) -> list[str]:
    """Pulls every custom_emoji_id out of a message's entities, in the
    order they appear, with duplicates removed. Looks at the target
    message if this is a reply, otherwise the message itself -- so
    `/emojiid` works whether you reply to an emoji or paste one right
    after the command."""
    target = message.reply_to_message if message.reply_to_message else message
    entities: list[MessageEntity] = (target.entities or []) + (target.caption_entities or [])
    seen: list[str] = []
    for entity in entities:
        if entity.type == "custom_emoji" and entity.custom_emoji_id not in seen:
            seen.append(entity.custom_emoji_id)
    return seen


def emoji_text(custom_emoji_id: str, fallback: str) -> CustomEmoji:
    """`fallback` is required by Telegram -- it's what's shown to
    non-Premium users, in system notifications, and if a Premium user
    forwards the message somewhere it can't render. Use the same emoji
    character the custom emoji is based on."""
    return CustomEmoji(fallback, custom_emoji_id=custom_emoji_id)
