"""
Renders a player's owned gifts for /stats -- the group version
(handlers/wallet.py) and the DM version (handlers/inbox.py) both call
this, so the two can no longer drift apart.

The layout here is deliberately a copy of what the DM /stats produced
before any of this was touched, because that version rendered correctly
in practice: header, one line per category listing every owned gift as
its own premium emoji tag, then the Worth line. No item counts, no tier
sub-grouping, no separate streak shelf -- just the badges.

WORTH_EMOJI_ID is kept as the exact hardcoded ID the DM version used
rather than being swapped for a registry key. It was previously suspected
of being the tag that made the group /stats fail to send, but the DM
version carried the same ID and rendered fine, which clears it. The tags
the group version had and the DM version did not were the streak line's
`bolt` icon and the d0-d9 digit glyphs -- that difference is the only
place the group-only failure can have come from.
"""
from app.database.models import Gift
from app.services.premium_emoji import pe, raw_tag
from app.utils.html_esc import esc

# Cabinet-value icon. Not in the EMOJI_IDS registry (it isn't a named UI
# icon, it's one-off catalog art), so it goes through raw_tag like the
# gift emoji themselves do.
WORTH_EMOJI_ID = "5375296873982604963"


def render_cabinet(cabinet: list[Gift], empty_hint: str) -> list[str]:
    """HTML lines for the gift cabinet block, header included.

    Every owned gift renders as its own <tg-emoji> tag -- the real badge
    art, one per gift, grouped under its category name. `empty_hint` is
    the nudge shown when the player owns nothing; it differs between
    group and DM because /shop is group-only.
    """
    lines = [f"{pe('vip')} <b>Gift Cabinet</b>"]

    if not cabinet:
        lines.append(empty_hint)
        return lines

    by_category: dict[str, list[Gift]] = {}
    for g in cabinet:
        by_category.setdefault(g.category, []).append(g)

    for category, gifts in by_category.items():
        tags = " ".join(raw_tag(g.emoji_id) for g in gifts)
        lines.append(f"{esc(category)}: {tags}")

    # Streak milestone badges are minted at price=0 (services/streak.py),
    # so they sit in the cabinet as badges without inflating its value --
    # Worth stays "what was actually spent in /shop".
    worth = sum(g.price for g in cabinet)
    lines.append("")
    lines.append("")
    lines.append(f"{raw_tag(WORTH_EMOJI_ID)} <b>Worth:</b> {worth:,}")
    return lines
