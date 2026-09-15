"""
The bot runs in HTML parse_mode (see app/bot.py). Anything interpolated
into outgoing message text that ISN'T a string this codebase wrote
itself -- a Telegram display name, chiefly -- has to be escaped before
going into an f-string, or a name containing '<', '>', or '&' breaks
Telegram's HTML parser and the whole message fails to send.

Call esc() at every point a .full_name / .display_name (or anything else
the user chose the value of) gets embedded in message text. Values this
codebase generates itself -- job names, amounts via format_amount(),
game names -- don't need it.
"""
from html import escape


def esc(value) -> str:
    return escape(str(value), quote=False)
