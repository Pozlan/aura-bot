"""
Maps semantic keys to Telegram custom (premium) emoji IDs, gathered via
/emojiid from the "Game Emoji" pack. pe(key) returns an HTML <tg-emoji>
tag to embed directly in message text -- this only works because the bot
now runs in HTML parse mode (see app/bot.py); it silently does nothing
under the old legacy-Markdown mode, which is why that switch had to
happen first.

`fallback` is what non-Premium viewers see, what shows in system
notifications, and what a message degrades to if forwarded somewhere
that can't render custom emoji. Picked to loosely match each icon's
theme -- not required to match the custom art pixel-for-pixel.
"""
EMOJI_IDS: dict[str, tuple[str, str]] = {
    "boss": ("5228962845672096235", "👹"),
    "crossed_swords": ("5454014806950429357", "⚔️"),
    "noob": ("5255738543673721267", "🐣"),
    "crit": ("5373342608028352831", "💥"),
    "rip": ("5463186335948878489", "💀"),
    "gold": ("5463046637842608206", "🪙"),
    "up": ("5463122435425448565", "📈"),
    "gg": ("5465465194056525619", "🎉"),
    "boom": ("5226813248900187912", "💣"),
    "rage": ("5463335865235288297", "😡"),
    "ko": ("5465137208878969279", "🥊"),
    "lol": ("5463121572137022242", "😂"),
    "buff": ("5462995330163289902", "💪"),
    "bg": ("5465198330558557107", "📉"),
    "wtf": ("5463139580934892960", "😳"),
    "res": ("5453870826761765894", "🔄"),
    "ns": ("5454177848203951217", "😅"),
    "sad": ("5463137996091962323", "😢"),
    "wp": ("5372957680174384345", "🤝"),
    "hype": ("5463412289883353404", "🚨"),
    "ban": ("5463358164705489689", "🚫"),
    "hit": ("5463156928307801722", "🎯"),
    "save": ("5462956611033117422", "🛡️"),
    "ez": ("5372965329511139384", "😎"),
    "pog": ("5375331860786200544", "🤯"),
    "loot": ("5463172695132745432", "🎁"),
    "l2p": ("5465225015190367274", "📉"),
    "top": ("5893048571560726748", "🏆"),
    "vip": ("5235695112419303615", "👑"),
    "cr8": ("5454092060527181056", "✅"),
    "play": ("5453921696354419743", "🎮"),
    "dart": ("5350460637182993292", "🎯"),
    "afk": ("5451732530048802485", "⏳"),
    "bff": ("5373110220232870002", "💸"),
    "aura": ("5852612609815093598", "💰"),
    "skull": ("5462882007451185227", "💀"),
    "wager": ("5226928895189598791", "🥷"),
    "bolt": ("5893450623449305489", "⚡"),
    "logo": ("5852612609815093598", "✨"),  # AURA mark, used in the DM /start intro

    # /streak digit glyphs -- used by render_number() below to spell out
    # the streak count digit-by-digit instead of plain text. Keyed "d0"-
    # "d9" (not bare "0"-"9") so they can't collide with any future
    # semantic key that happens to be a digit string.
    "d0": ("5447357736490649384", "0️⃣"),
    "d1": ("5447584416274595624", "1️⃣"),
    "d2": ("5447569199205468152", "2️⃣"),
    "d3": ("5438196446694228650", "3️⃣"),
    "d4": ("5435882198056060129", "4️⃣"),
    "d5": ("5447616284931933807", "5️⃣"),
    "d6": ("5447377755333214518", "6️⃣"),
    "d7": ("5447609687862165448", "7️⃣"),
    "d8": ("5447218643974767663", "8️⃣"),
    "d9": ("5447303753046703974", "9️⃣"),
}


def pe(key: str) -> str:
    """Returns an inline <tg-emoji> HTML tag for `key`. An unmapped key
    falls back to a plain '❓' instead of raising, so a typo here degrades
    a message instead of crashing it."""
    if key not in EMOJI_IDS:
        return "❓"
    custom_id, fallback = EMOJI_IDS[key]
    return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'


def raw_tag(emoji_id: str, fallback: str = "🎁") -> str:
    """Same as pe(), but for an emoji ID that isn't in the semantic
    EMOJI_IDS registry above -- used for /shop gifts, where the IDs are
    arbitrary catalog data (from /addgift) rather than named UI icons."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def render_number(n: int) -> str:
    """Spells out `n` as a run of the custom d0-d9 digit glyphs (e.g. 23
    -> the '2' glyph followed by the '3' glyph) instead of plain text.
    Used by /streak to show the current streak count."""
    return "".join(pe(f"d{ch}") for ch in str(n))


# /streak count, 1-10, one dedicated glyph per value -- as given, not
# spelled digit-by-digit. Reuses the same ten IDs as d1-d9/d0 above
# (10 -> the same asset as d0) since that's the exact list supplied for
# this; it's a separate name so a call site is explicit about which
# number it's rendering (the streak count) rather than an arbitrary digit
# string.
# value 1-10 -> (emoji_id, fallback char). 10's fallback is "0" because
# it reuses the exact same asset as the d0 digit glyph above (that's the
# ID given for it) -- the fallback has to match what that asset actually
# depicts, not the word "10", or a degraded send would show the wrong
# character for it.
STREAK_COUNT_IDS: dict[int, tuple[str, str]] = {
    1: EMOJI_IDS["d1"],
    2: EMOJI_IDS["d2"],
    3: EMOJI_IDS["d3"],
    4: EMOJI_IDS["d4"],
    5: EMOJI_IDS["d5"],
    6: EMOJI_IDS["d6"],
    7: EMOJI_IDS["d7"],
    8: EMOJI_IDS["d8"],
    9: EMOJI_IDS["d9"],
    10: EMOJI_IDS["d0"],
}


def render_streak_count(n: int) -> str:
    """The streak-count number ONLY (current streak, best run) -- this is
    the one place the dedicated 1-10 glyphs are used. n=1..10 sends the
    single matching glyph directly. n=11+ isn't covered by the supplied
    list (milestones run up to 300 days), so it falls back to spelling
    the number out digit-by-digit with the same underlying assets --
    flag if a single-glyph scheme for the full range is wanted instead,
    that needs more IDs than the 10 given.

    ONLY the streak count goes through this. Cooldown remaining time
    ("3h 12m") and other numbers in /streak stay plain text -- narrower
    than the previous version, which spelled every number in the message
    in glyphs. Fewer custom-emoji tags per message means fewer chances
    for one bad ID to take down the whole send, which matters here since
    this exact message has been failing since before any of these edits."""
    if 1 <= n <= 10:
        custom_id, fallback = STREAK_COUNT_IDS[n]
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return "".join(pe(f"d{ch}") for ch in str(n))
