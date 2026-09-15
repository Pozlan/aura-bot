"""Spec section 32: reply-based commands are the primary UX for /rob, /tip, /pvp.
Centralized here so every handler resolves targets the same way."""
from aiogram.types import Message, User as TgUser


def resolve_reply_target(message: Message) -> TgUser | None:
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        if target.is_bot:
            return None
        return target
    return None
