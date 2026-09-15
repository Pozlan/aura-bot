"""
/shop service layer (spec: aura sink + pure-flex status system). Every gift
is a single, unique, one-of-one row -- not a "type" with a quantity. Once
bought it's permanently sold; the only way a sold-out gift becomes
available again is the owner adding a brand NEW row via /addgift, never by
un-selling the old one.

Categories are entirely data-driven from what's actually in the `gifts`
table -- no separate config of "which categories exist" to keep in sync.
A category has tiers if ANY of its rows have a non-null tier (Low/Mid/High);
otherwise it's treated as Limited Edition (flat item list, no tier step).
"""
import random
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ECONOMY
from app.database.models import Gift, PlayerState
from app.services.economy import adjust_balance, available_balance
from app.services.premium_emoji import raw_tag
from app.utils.time import utcnow

TIER_ORDER = {"low": 0, "mid": 1, "high": 2}


class GiftError(ValueError):
    pass


async def get_categories(session: AsyncSession) -> list[dict]:
    """One entry per category: name, whether it has tiers, and a preview
    emoji (a HIGH-tier item for tiered categories -- picked deterministically
    so /shop looks the same each time; a genuinely random item for Limited
    Edition, since there's no 'high tier' to anchor on for those)."""
    # "streak" is reserved for /streak milestone badges (see
    # services/streak.py::mint_streak_gift) -- earned, never sold, so it
    # never shows up as a browsable /shop category even though the rows
    # live in the same `gifts` table.
    all_gifts = list((await session.execute(select(Gift).where(Gift.category != "streak"))).scalars())
    by_category: dict[str, list[Gift]] = {}
    for g in all_gifts:
        by_category.setdefault(g.category, []).append(g)

    categories = []
    for name, gifts in by_category.items():
        has_tiers = any(g.tier for g in gifts)
        if has_tiers:
            high = [g for g in gifts if g.tier == "high"]
            preview = min(high, key=lambda g: g.id) if high else min(gifts, key=lambda g: g.id)
        else:
            preview = random.choice(gifts)
        categories.append({"category": name, "has_tiers": has_tiers, "preview_emoji_id": preview.emoji_id})
    categories.sort(key=lambda c: c["category"])
    return categories


async def get_tiers(session: AsyncSession, category: str) -> list[dict]:
    """Low/Mid/High for a tiered category, each with its flat price
    (every item in a tier costs the same) and how many are still available."""
    stmt = select(Gift).where(Gift.category == category)
    gifts = list((await session.execute(stmt)).scalars())
    by_tier: dict[str, list[Gift]] = {}
    for g in gifts:
        by_tier.setdefault(g.tier, []).append(g)

    tiers = []
    for tier, items in by_tier.items():
        unsold = [g for g in items if g.owner_user_id is None]
        available = len(unsold)
        # Prefer an unsold item's price -- that's what a buyer actually
        # pays right now. /setprice only ever updates unsold rows, so once
        # a tier has a price split (old sold stock vs a repriced restock),
        # items[0] with no ORDER BY isn't reliably "the current price"
        # anymore. Only fall back to any item's price when fully sold out,
        # just to have something to display.
        price = unsold[0].price if unsold else items[0].price
        tiers.append({
            "tier": tier,
            "price": price,
            "available": available,
            "total": len(items),
        })
    tiers.sort(key=lambda t: TIER_ORDER.get(t["tier"], 99))
    return tiers


async def get_items(session: AsyncSession, category: str, tier: str | None) -> list[Gift]:
    """Every item in a category (+tier, if given), sold or not -- the shop
    displays sold ones marked SOLD rather than hiding them."""
    stmt = select(Gift).where(Gift.category == category, Gift.tier == tier).order_by(Gift.id)
    return list((await session.execute(stmt)).scalars())


async def get_gift(session: AsyncSession, gift_id: int) -> Gift | None:
    return await session.get(Gift, gift_id)


async def purchase_gift(session: AsyncSession, state: PlayerState, gift: Gift, group_id: int) -> None:
    """Atomic compare-and-swap: the UPDATE only succeeds if the gift is
    STILL unowned at the moment this runs, closing the race where two
    people tap the same gift within the same instant. Raises GiftError for
    every rejection path (already sold / can't afford) so the handler can
    show the right message without needing to re-check anything itself."""
    if gift.owner_user_id is not None:
        raise GiftError("sold")
    if gift.price > available_balance(state):
        raise GiftError("broke")

    result = await session.execute(
        update(Gift)
        .where(Gift.id == gift.id, Gift.owner_user_id.is_(None))
        .values(owner_user_id=state.user_id, purchased_at=utcnow())
    )
    if result.rowcount == 0:
        raise GiftError("sold")  # someone else bought it a moment before this

    await adjust_balance(session, state, -gift.price, "shop", ref=f"gift#{gift.id}", group_id=group_id)


async def sell_back(session: AsyncSession, state: PlayerState, gift: Gift, group_id: int) -> int:
    """Sells an owned gift back to the shop (not to another player) for
    ECONOMY.GIFT_REFUND_RATE of the category/tier's CURRENT going price --
    not what this player originally paid. If the price has gone up since
    they bought it, they profit on the sale. That's the incentive to buy
    early and sell later, not a guaranteed loss for holding a gift while
    it appreciates. The gift is also relisted at that current price (not
    left at its old one), so it doesn't undercut the rest of the shelf --
    see handlers/shop.py's item listing, which now hides sold items
    entirely rather than showing SOLD, so a stale-priced returned item
    would otherwise sit there looking like a normal, cheaper option.

    If the gift being sold is the seller's currently equipped badge, that
    gets cleared too -- otherwise their equipped_gift_id would keep
    pointing at a gift that's either unowned or, worse, owned by whoever
    buys it next."""
    if gift.owner_user_id != state.user_id:
        raise GiftError("not_yours")

    siblings = list((await session.execute(
        select(Gift).where(Gift.category == gift.category, Gift.tier == gift.tier)
    )).scalars())
    unsold = [g for g in siblings if g.owner_user_id is None]
    # If nothing else is currently unsold (whole tier's sold out), there's
    # no divergent "current price" to chase -- /setprice refuses to touch
    # a fully sold-out tier, so every row in it is still at the same
    # price. Falling back to this gift's own price is exactly that value.
    current_price = unsold[0].price if unsold else gift.price

    refund = int(current_price * ECONOMY.GIFT_REFUND_RATE)

    if state.equipped_gift_id == gift.id:
        state.equipped_gift_id = None

    gift.owner_user_id = None
    gift.purchased_at = None
    gift.price = current_price

    await adjust_balance(session, state, refund, "shop", ref=f"sold back gift#{gift.id}", group_id=group_id)
    return refund


async def delete_gift(session: AsyncSession, gift_id: int) -> Gift | None:
    """Permanently removes an unsold gift row from the shop. Refuses (returns
    None) if the gift doesn't exist or is already owned -- deleting a sold
    gift would silently take it away from whoever bought it, badge and all,
    with no way to undo it. Only ever touches stock nobody has bought yet."""
    gift = await session.get(Gift, gift_id)
    if gift is None or gift.owner_user_id is not None:
        return None
    await session.delete(gift)
    return gift


async def get_stock_overview(session: AsyncSession) -> list[dict]:
    """Full stock breakdown, every category, every tier -- for /stock.
    Same category/tier grouping logic as get_categories/get_tiers, just
    fetched once for everything at once instead of one category at a time."""
    all_gifts = list((await session.execute(select(Gift))).scalars())
    by_category: dict[str, list[Gift]] = {}
    for g in all_gifts:
        by_category.setdefault(g.category, []).append(g)

    overview = []
    for category, gifts in sorted(by_category.items()):
        has_tiers = any(g.tier for g in gifts)
        if has_tiers:
            by_tier: dict[str, list[Gift]] = {}
            for g in gifts:
                by_tier.setdefault(g.tier, []).append(g)
            tier_rows = []
            for tier, items in by_tier.items():
                unsold = [g for g in items if g.owner_user_id is None]
                available = len(unsold)
                # Same fix as get_tiers() above -- prefer an unsold item's
                # price so /stock reflects a /setprice change, not a stale
                # sold item's original price.
                price = unsold[0].price if unsold else items[0].price
                tier_rows.append({"tier": tier, "available": available, "total": len(items), "price": price})
            tier_rows.sort(key=lambda t: TIER_ORDER.get(t["tier"], 99))
            overview.append({"category": category, "tiers": tier_rows, "flat": None})
        else:
            unsold = [g for g in gifts if g.owner_user_id is None]
            available = len(unsold)
            price = unsold[0].price if unsold else gifts[0].price
            overview.append({
                "category": category, "tiers": None,
                "flat": {"available": available, "total": len(gifts), "price": price},
            })
    return overview


async def player_cabinet(session: AsyncSession, user_id: int) -> list[Gift]:
    stmt = select(Gift).where(Gift.owner_user_id == user_id).order_by(Gift.category, Gift.id)
    return list((await session.execute(stmt)).scalars())


async def badge_tag(session: AsyncSession, state: PlayerState) -> str:
    """Returns the equipped gift's emoji tag, or '' if nothing's equipped.
    Deliberately tolerant: if the equipped gift somehow no longer exists,
    this returns '' instead of raising, so a badge issue never breaks
    /stats, /bal, /top, or /gtop."""
    if state.equipped_gift_id is None:
        return ""
    gift = await session.get(Gift, state.equipped_gift_id)
    if gift is None:
        return ""
    return " " + raw_tag(gift.emoji_id)
