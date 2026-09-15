from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def challenge_keyboard(challenge_id: int, wager: int, house_available: bool) -> InlineKeyboardMarkup:
    # Telegram buttons render plain text ONLY -- no HTML, no custom emoji.
    # format_amount() includes a <tg-emoji> tag, which showed up as raw
    # text on the button ("Accept · 1,000 <tg-emoji...>"). The amount is
    # already visible in the message text above the button, so the label
    # just needs to say what the button does.
    rows = [[InlineKeyboardButton(text="Accept", callback_data=f"acc:{challenge_id}")]]
    if house_available:
        rows.append([InlineKeyboardButton(text="Play vs Bot", callback_data=f"vsbot:{challenge_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rps_choice_keyboard(challenge_id: int) -> InlineKeyboardMarkup:
    # Emoji only, no words -- the emoji already say rock/paper/scissors on
    # their own, text next to them was just visual clutter.
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✊", callback_data=f"rps:{challenge_id}:rock"),
        InlineKeyboardButton(text="✋", callback_data=f"rps:{challenge_id}:paper"),
        InlineKeyboardButton(text="✌️", callback_data=f"rps:{challenge_id}:scissors"),
    ]])


def coin_choice_keyboard(challenge_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🟡 Heads", callback_data=f"coin:{challenge_id}:heads"),
        InlineKeyboardButton(text="⚪ Tails", callback_data=f"coin:{challenge_id}:tails"),
    ]])
    
