"""
/shop -- the aura sink + flex system. Flow: categories -> (tier, if the
category has one) -> numbered items -> tap to buy. Every step is buttons,
not typed input (consistent with every other choice in the bot, and more
reliable in a busy group chat than parsing free text).

/addgift is the owner-only restock tool. /equip lets a player pick which
OWNED gift displays as their badge (see gifts.badge_tag, shown in /stats,
/bal, /top, /gtop).
"""
import random

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import settings, ECONOMY
from app.database.db import get_session
from app.services.economy import get_or_create_user, get_or_create_state, format_amount, parse_amount, InvalidAmount
from app.services.gifts import (
    get_categories, get_tiers, get_items, get_gift, purchase_gift, sell_back, player_cabinet,
    get_stock_overview, delete_gift, GiftError,
)
from app.services.premium_emoji import pe, raw_tag
from app.utils.html_esc import esc

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

TIER_LABEL = {"low": "Low", "mid": "Mid", "high": "High"}
TROLL_LINES = [
    "lol you thought? go run a few more /hunt first.",
    "bro checked his balance and still pressed buy 💀",
    "that's cute. come back when you have that much.",
    "the audacity. you're nowhere close.",
    "not happening on that balance, champ.",
]

# /addgift button-flow state, owner_id -> {"category", "tier", "stage"}.
# In-memory on purpose -- this is an interactive, one-sitting owner tool,
# not persistent data, so it resets on a redeploy/restart. That's fine:
# worst case is just re-running /addgift.
_pending: dict[int, dict] = {}


def _category_kb(categories: list[dict]) -> InlineKeyboardMarkup:
    # Numbers only -- the category names are already spelled out in the
    # message text right above these buttons (see _categories_view), so
    # repeating the full name on the button itself was just dead width.
    buttons = [
        InlineKeyboardButton(text=str(i), callback_data=f"shop:cat:{c['category']}")
        for i, c in enumerate(categories, start=1)
    ]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    rows.append([InlineKeyboardButton(text="cancel", callback_data="shop:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tier_kb(category: str, tiers: list[dict]) -> InlineKeyboardMarkup:
    # Short label only (Low/Mid/High) -- price and stock count are already
    # in the message text above (see _tiers_view), same reasoning as
    # category buttons. All 3 tiers fit one row instead of stacking tall.
    buttons = [
        InlineKeyboardButton(text=TIER_LABEL[t['tier']], callback_data=f"shop:tier:{category}:{t['tier']}")
        for t in tiers
    ]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    rows.append([InlineKeyboardButton(text="« back", callback_data="shop:back:categories")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _item_kb(unsold: list, category: str, tier: str | None) -> InlineKeyboardMarkup:
    """unsold must already be filtered to unowned gifts -- see _show_items."""
    buttons = [
        InlineKeyboardButton(text=str(i), callback_data=f"shop:item:{g.id}")
        for i, g in enumerate(unsold, start=1)
    ]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    # Tiered category -> back goes to its tier list. Limited Edition (no
    # tier step at all) -> back goes straight to categories.
    back_cb = f"shop:back:tier:{category}" if tier else "shop:back:categories"
    rows.append([InlineKeyboardButton(text="« back", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _categories_view(categories: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"{pe('vip')} <b>AURA SHOP</b>", ""]
    for i, c in enumerate(categories, start=1):
        lines.append(f"{i}. <b>{esc(c['category'])}</b> {raw_tag(c['preview_emoji_id'])}")
    lines.append("")
    lines.append("tap a category.")
    return "\n".join(lines), _category_kb(categories)


def _tiers_view(category: str, tiers: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"<b>{esc(category)}</b>", ""]
    for t in tiers:
        lines.append(f"{TIER_LABEL[t['tier']]} · {format_amount(t['price'])} aura each ({t['available']}/{t['total']} left)")
    lines.append("")
    lines.append("tap a tier.")
    return "\n".join(lines), _tier_kb(category, tiers)


@router.message(Command("shop"))
async def shop_cmd(message: Message):
    async with get_session() as session:
        categories = await get_categories(session)

    if not categories:
        await message.reply("shop's empty right now. check back later.")
        return

    text, kb = _categories_view(categories)
    await message.reply(text, reply_markup=kb)


@router.callback_query(F.data == "shop:cancel")
async def on_shop_cancel(callback: CallbackQuery):
    await callback.message.edit_text("cancelled.")
    await callback.answer()


@router.callback_query(F.data == "shop:back:categories")
async def on_back_categories(callback: CallbackQuery):
    async with get_session() as session:
        categories = await get_categories(session)
    if not categories:
        await callback.message.edit_text("shop's empty right now. check back later.")
    else:
        text, kb = _categories_view(categories)
        await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("shop:cat:"))
async def on_category(callback: CallbackQuery):
    category = callback.data.split(":", 2)[2]
    async with get_session() as session:
        categories = await get_categories(session)
        meta = next((c for c in categories if c["category"] == category), None)
        has_tiers = bool(meta and meta["has_tiers"])
        tiers = await get_tiers(session, category) if has_tiers else []

    if has_tiers:
        text, kb = _tiers_view(category, tiers)
        await callback.message.edit_text(text, reply_markup=kb)
    else:
        # Limited Edition -- no tier step, straight to the item list
        await _show_items(callback, category, None)
    await callback.answer()


@router.callback_query(F.data.startswith("shop:back:tier:"))
async def on_back_tier(callback: CallbackQuery):
    category = callback.data.split(":", 3)[3]
    async with get_session() as session:
        tiers = await get_tiers(session, category)
    text, kb = _tiers_view(category, tiers)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("shop:tier:"))
async def on_tier(callback: CallbackQuery):
    _, _, category, tier = callback.data.split(":", 3)
    await _show_items(callback, category, tier)
    await callback.answer()


async def _show_items(callback: CallbackQuery, category: str, tier: str | None):
    async with get_session() as session:
        items = await get_items(session, category, tier)

    # Sold items don't appear at all anymore -- not even struck through.
    # Numbers are sequential over what's actually buyable right now, not
    # over the full historical row set.
    unsold = [g for g in items if g.owner_user_id is None]
    label = f"{esc(category)} · {TIER_LABEL[tier]}" if tier else esc(category)

    if not unsold:
        back_cb = f"shop:back:tier:{category}" if tier else "shop:back:categories"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« back", callback_data=back_cb)]])
        await callback.message.edit_text(f"<b>{label}</b>\n\nsold out. check back later.", reply_markup=kb)
        return

    lines = [f"<b>{label}</b>", ""]
    for i, g in enumerate(unsold, start=1):
        lines.append(f"{i}. {raw_tag(g.emoji_id)} · {format_amount(g.price)} aura")
    lines.append("")
    lines.append("tap a number to buy.")
    await callback.message.edit_text("\n".join(lines), reply_markup=_item_kb(unsold, category, tier))


@router.callback_query(F.data.startswith("shop:item:"))
async def on_item(callback: CallbackQuery):
    gift_id = int(callback.data.split(":", 2)[2])
    user = callback.from_user

    async with get_session() as session:
        await get_or_create_user(session, user.id, user.full_name, user.username)
        state = await get_or_create_state(session, user.id, callback.message.chat.id)
        gift = await get_gift(session, gift_id)
        if gift is None:
            await callback.answer("that gift doesn't exist anymore.", show_alert=True)
            return
        try:
            await purchase_gift(session, state, gift, callback.message.chat.id)
        except GiftError as e:
            if str(e) == "sold":
                await callback.answer("too slow, someone already grabbed that one.", show_alert=True)
            else:
                await callback.answer(random.choice(TROLL_LINES), show_alert=True)
            return

    await callback.answer("sold! check your DMs... just kidding, check below.")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(raw_tag(gift.emoji_id))
    await callback.message.answer(
        f"{pe('gg')} <b>{esc(user.full_name)}</b> just copped it. added to your /stats cabinet forever."
    )


SELLBACK_PAGE_SIZE = 10


def _sellback_view(owned: list, page: int) -> tuple[str, InlineKeyboardMarkup]:
    total_pages = max(1, (len(owned) + SELLBACK_PAGE_SIZE - 1) // SELLBACK_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * SELLBACK_PAGE_SIZE
    page_items = owned[start:start + SELLBACK_PAGE_SIZE]

    lines = [f"sell back for {int(ECONOMY.GIFT_REFUND_RATE * 100)}% of price. pick one:", ""]
    for i, g in enumerate(page_items, start=start + 1):
        lines.append(f"{i}. {raw_tag(g.emoji_id)} ({esc(g.category)}) · {format_amount(g.price)}")
    if total_pages > 1:
        lines.append("")
        lines.append(f"page {page + 1}/{total_pages}")

    buttons = [
        InlineKeyboardButton(text=str(i), callback_data=f"sb:pick:{g.id}")
        for i, g in enumerate(page_items, start=start + 1)
    ]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="« prev", callback_data=f"sb:page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="next »", callback_data=f"sb:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="cancel", callback_data="sb:cancel")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("sellback"))
async def sellback_cmd(message: Message):
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        owned = await player_cabinet(session, user.id)

    if not owned:
        await message.reply("you don't own any gifts to sell back.")
        return

    text, kb = _sellback_view(owned, page=0)
    await message.reply(text, reply_markup=kb)


@router.callback_query(F.data.startswith("sb:page:"))
async def on_sellback_page(callback: CallbackQuery):
    page = int(callback.data.split(":", 2)[2])
    async with get_session() as session:
        owned = await player_cabinet(session, callback.from_user.id)

    if not owned:
        await callback.message.edit_text("you don't own any gifts to sell back.")
        await callback.answer()
        return

    text, kb = _sellback_view(owned, page)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("sb:pick:"))
async def on_sellback_pick(callback: CallbackQuery):
    gift_id = int(callback.data.split(":", 2)[2])
    async with get_session() as session:
        gift = await get_gift(session, gift_id)
        if gift is None or gift.owner_user_id != callback.from_user.id:
            await callback.answer("that's not yours (anymore?).", show_alert=True)
            return
        refund = int(gift.price * ECONOMY.GIFT_REFUND_RATE)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="yes, sell it", callback_data=f"sb:confirm:{gift_id}"),
        InlineKeyboardButton(text="cancel", callback_data="sb:cancel"),
    ]])
    await callback.message.edit_text(
        f"sell {raw_tag(gift.emoji_id)} back for {format_amount(refund)}? this can't be undone.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sb:confirm:"))
async def on_sellback_confirm(callback: CallbackQuery):
    gift_id = int(callback.data.split(":", 2)[2])
    user = callback.from_user

    async with get_session() as session:
        state = await get_or_create_state(session, user.id, callback.message.chat.id)
        gift = await get_gift(session, gift_id)
        if gift is None:
            await callback.answer("that gift doesn't exist anymore.", show_alert=True)
            return
        try:
            refund = await sell_back(session, state, gift, callback.message.chat.id)
        except GiftError:
            await callback.answer("that's not yours (anymore?).", show_alert=True)
            return

    await callback.message.edit_text(f"sold. +{format_amount(refund)}")
    await callback.answer("sold!")


@router.callback_query(F.data == "sb:cancel")
async def on_sellback_cancel(callback: CallbackQuery):
    await callback.message.edit_text("cancelled, still yours.")
    await callback.answer()


def _equip_view(owned: list, page: int) -> tuple[str, InlineKeyboardMarkup]:
    total_pages = max(1, (len(owned) + SELLBACK_PAGE_SIZE - 1) // SELLBACK_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * SELLBACK_PAGE_SIZE
    page_items = owned[start:start + SELLBACK_PAGE_SIZE]

    lines = ["pick a badge to display next to your name:", ""]
    for i, g in enumerate(page_items, start=start + 1):
        lines.append(f"{i}. {raw_tag(g.emoji_id)} ({esc(g.category)})")
    if total_pages > 1:
        lines.append("")
        lines.append(f"page {page + 1}/{total_pages}")

    buttons = [
        InlineKeyboardButton(text=str(i), callback_data=f"equip:{g.id}")
        for i, g in enumerate(page_items, start=start + 1)
    ]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="« prev", callback_data=f"equip:page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="next »", callback_data=f"equip:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="remove badge", callback_data="equip:none")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("equip"))
async def equip_cmd(message: Message):
    async with get_session() as session:
        user = message.from_user
        await get_or_create_user(session, user.id, user.full_name, user.username)
        owned = await player_cabinet(session, user.id)

    if not owned:
        await message.reply("you don't own any gifts yet. check /shop.")
        return

    text, kb = _equip_view(owned, page=0)
    await message.reply(text, reply_markup=kb)


@router.callback_query(F.data.startswith("equip:page:"))
async def on_equip_page(callback: CallbackQuery):
    page = int(callback.data.split(":", 2)[2])
    async with get_session() as session:
        owned = await player_cabinet(session, callback.from_user.id)

    if not owned:
        await callback.message.edit_text("you don't own any gifts yet. check /shop.")
        await callback.answer()
        return

    text, kb = _equip_view(owned, page)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("equip:") & ~F.data.startswith("equip:page:"))
async def on_equip(callback: CallbackQuery):
    choice = callback.data.split(":", 1)[1]
    user = callback.from_user

    async with get_session() as session:
        state = await get_or_create_state(session, user.id, callback.message.chat.id)
        if choice == "none":
            state.equipped_gift_id = None
            await callback.answer("badge removed.")
        else:
            gift_id = int(choice)
            owned = await player_cabinet(session, user.id)
            if gift_id not in [g.id for g in owned]:
                await callback.answer("that's not yours.", show_alert=True)
                return
            state.equipped_gift_id = gift_id
            await callback.answer("badge equipped!")

    await callback.message.edit_reply_markup(reply_markup=None)


@router.message(Command("addgift"))
async def addgift_cmd(message: Message):
    """Owner-only restock tool. Bare /addgift (no args) starts the button
    flow: pick category -> pick tier -> type price + emoji id(s). Typing
    the old pipe-syntax directly still works too, for anyone who prefers
    it: /addgift <category> | <tier or -> | <price> | <emoji_id> [...]"""
    if message.from_user.id not in settings.owner_id_set:
        return  # silently ignore -- no error text, so it doesn't hint the command exists

    raw = message.text.split(maxsplit=1)
    if len(raw) >= 2 and "|" in raw[1]:
        await _addgift_from_pipes(message, raw[1])
        return

    async with get_session() as session:
        categories = await get_categories(session)

    _pending.pop(message.from_user.id, None)
    rows = [
        [InlineKeyboardButton(text=c["category"], callback_data=f"ag:cat:{c['category']}")]
        for c in categories
    ]
    rows.append([InlineKeyboardButton(text="➕ new category", callback_data="ag:newcat")])
    await message.reply("add stock to which category?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _addgift_from_pipes(message: Message, raw: str):
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) != 4:
        await message.reply("need exactly 4 parts separated by | -- category, tier, price, emoji id(s).")
        return

    category, tier_raw, price_raw, ids_raw = parts
    tier = None if tier_raw in ("-", "none", "") else tier_raw.lower()
    if tier is not None and tier not in ("low", "mid", "high"):
        await message.reply("tier must be exactly one of: low, mid, high (or - for no tiers).")
        return
    try:
        price = parse_amount(price_raw)
    except InvalidAmount as e:
        await message.reply(f"bad price: {e}")
        return

    emoji_ids = ids_raw.split()
    if not emoji_ids:
        await message.reply("no emoji ids given.")
        return

    await _create_gifts(category, tier, price, emoji_ids)
    await message.reply(f"added {len(emoji_ids)} gift(s) to {esc(category)} ({tier or 'limited'}).")


@router.callback_query(F.data.startswith("ag:cat:"))
async def on_addgift_category(callback: CallbackQuery):
    category = callback.data.split(":", 2)[2]
    async with get_session() as session:
        categories = await get_categories(session)
        meta = next((c for c in categories if c["category"] == category), None)
        has_tiers = bool(meta and meta["has_tiers"])
        tiers = await get_tiers(session, category) if has_tiers else []

    _pending[callback.from_user.id] = {"category": category}

    if has_tiers:
        rows = [
            [InlineKeyboardButton(text=f"{TIER_LABEL[t['tier']]} ({t['available']}/{t['total']})", callback_data=f"ag:tier:{t['tier']}")]
            for t in tiers
        ]
        rows.append([InlineKeyboardButton(text="➕ new tier", callback_data="ag:newtier")])
        await callback.message.edit_text(f"<b>{esc(category)}</b> -- which tier?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        _pending[callback.from_user.id]["tier"] = None
        _pending[callback.from_user.id]["stage"] = "final"
        await callback.message.edit_text(
            f"<b>{esc(category)}</b> (limited edition)\n\nnow send: <code>&lt;price&gt; &lt;emoji_id&gt; [more ids...]</code>"
        )
    await callback.answer()


@router.callback_query(F.data == "ag:newcat")
async def on_addgift_new_category(callback: CallbackQuery):
    _pending[callback.from_user.id] = {"stage": "new_category_line"}
    await callback.message.edit_text(
        "send the new category as one line:\n<code>&lt;category name&gt; | &lt;tier or -&gt;</code>\n"
        "example: <code>Basketballs | low</code> (use <code>-</code> for a Limited Edition category, no tiers)"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ag:tier:"))
async def on_addgift_tier(callback: CallbackQuery):
    tier = callback.data.split(":", 2)[2]
    pending = _pending.get(callback.from_user.id)
    if not pending or "category" not in pending:
        await callback.answer("session expired, run /addgift again.", show_alert=True)
        return
    pending["tier"] = tier
    pending["stage"] = "final"
    await callback.message.edit_text(
        f"<b>{esc(pending['category'])} · {TIER_LABEL.get(tier, tier)}</b>\n\n"
        "now send: <code>&lt;price&gt; &lt;emoji_id&gt; [more ids...]</code>"
    )
    await callback.answer()


@router.callback_query(F.data == "ag:newtier")
async def on_addgift_new_tier(callback: CallbackQuery):
    pending = _pending.get(callback.from_user.id)
    if not pending or "category" not in pending:
        await callback.answer("session expired, run /addgift again.", show_alert=True)
        return
    pending["stage"] = "new_tier_name"
    await callback.message.edit_text("send the new tier name (e.g. <code>low</code>, <code>mid</code>, <code>high</code>):")
    await callback.answer()


def _awaiting_addgift_input(message: Message) -> bool:
    """Named filter (not a bare catch-all) -- only ever matches an owner
    who is mid-way through the /addgift button flow. Everyone else, and
    every other message in the group, passes straight through untouched."""
    return (
        message.text is not None
        and not message.text.startswith("/")
        and message.from_user is not None
        and message.from_user.id in settings.owner_id_set
        and message.from_user.id in _pending
    )


@router.message(_awaiting_addgift_input)
async def on_addgift_text(message: Message):
    pending = _pending[message.from_user.id]
    stage = pending.get("stage")

    if stage == "new_category_line":
        parts = [p.strip() for p in message.text.split("|")]
        if len(parts) != 2:
            await message.reply("need exactly: category | tier (or -). try again.")
            return
        category, tier_raw = parts
        tier = None if tier_raw in ("-", "none", "") else tier_raw.lower()
        if tier is not None and tier not in ("low", "mid", "high"):
            await message.reply("tier must be exactly one of: low, mid, high (or - for no tiers).")
            return
        pending["category"] = category
        pending["tier"] = tier
        pending["stage"] = "final"
        await message.reply("got it. now send: <code>&lt;price&gt; &lt;emoji_id&gt; [more ids...]</code>")
        return

    if stage == "new_tier_name":
        tier = message.text.strip().lower()
        if tier not in ("low", "mid", "high"):
            await message.reply("tier must be exactly one of: low, mid, high.")
            return
        pending["tier"] = tier
        pending["stage"] = "final"
        await message.reply("got it. now send: <code>&lt;price&gt; &lt;emoji_id&gt; [more ids...]</code>")
        return

    if stage == "final":
        parts = message.text.split()
        if len(parts) < 2:
            await message.reply("need at least: &lt;price&gt; &lt;emoji_id&gt;")
            return
        try:
            price = parse_amount(parts[0])
        except InvalidAmount as e:
            await message.reply(f"bad price: {e}")
            return
        emoji_ids = parts[1:]

        category, tier = pending["category"], pending.get("tier")
        await _create_gifts(category, tier, price, emoji_ids)
        _pending.pop(message.from_user.id, None)
        await message.reply(f"added {len(emoji_ids)} gift(s) to {esc(category)} ({tier or 'limited'}) at {format_amount(price)} each.")


async def _create_gifts(category: str, tier: str | None, price: int, emoji_ids: list[str]) -> None:
    from app.database.models import Gift
    async with get_session() as session:
        for eid in emoji_ids:
            session.add(Gift(category=category, tier=tier, emoji_id=eid, price=price))


# /setprice -- owner-only. Bulk-updates the price of every UNSOLD gift in
# a category (+ tier, if it has one). Sold gifts keep whatever price they
# were actually bought at -- that's a historical record on the Gift row,
# not something to silently rewrite out from under a past sale. Mirrors
# /removegift's flow: category -> tier -> type new price -> confirm.
_pending_price: dict[int, dict] = {}


@router.message(Command("setprice"))
async def setprice_cmd(message: Message):
    if message.from_user.id not in settings.owner_id_set:
        return  # silently ignore -- no error text, so it doesn't hint the command exists

    async with get_session() as session:
        categories = await get_categories(session)
    if not categories:
        await message.reply("shop's empty, nothing to reprice.")
        return

    _pending_price.pop(message.from_user.id, None)
    rows = [
        [InlineKeyboardButton(text=c["category"], callback_data=f"sp:cat:{c['category']}")]
        for c in categories
    ]
    rows.append([InlineKeyboardButton(text="cancel", callback_data="sp:cancel")])
    await message.reply("change the price of which category?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "sp:cancel")
async def on_setprice_cancel(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    _pending_price.pop(callback.from_user.id, None)
    await callback.message.edit_text("cancelled, nothing changed.")
    await callback.answer()


@router.callback_query(F.data.startswith("sp:cat:"))
async def on_setprice_category(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    category = callback.data.split(":", 2)[2]
    async with get_session() as session:
        categories = await get_categories(session)
        meta = next((c for c in categories if c["category"] == category), None)
        has_tiers = bool(meta and meta["has_tiers"])
        tiers = await get_tiers(session, category) if has_tiers else []
        items = await get_items(session, category, None) if not has_tiers else []

    if has_tiers:
        rows = [
            [InlineKeyboardButton(
                # Plain f"{n:,}" here, NOT format_amount() -- buttons only
                # render plain text, so format_amount()'s embedded
                # <tg-emoji> tag would show up literally instead of
                # rendering as an emoji.
                text=f"{TIER_LABEL[t['tier']]} (currently {t['price']:,})",
                callback_data=f"sp:tier:{category}:{t['tier']}",
            )]
            for t in tiers
        ]
        rows.append([InlineKeyboardButton(text="cancel", callback_data="sp:cancel")])
        await callback.message.edit_text(f"<b>{esc(category)}</b> -- which tier?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        current = items[0].price if items else 0
        _pending_price[callback.from_user.id] = {"category": category, "tier": None}
        await callback.message.edit_text(f"<b>{esc(category)}</b> (currently {format_amount(current)})\n\nsend the new price:")
    await callback.answer()


@router.callback_query(F.data.startswith("sp:tier:"))
async def on_setprice_tier(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    _, _, category, tier = callback.data.split(":", 3)
    _pending_price[callback.from_user.id] = {"category": category, "tier": tier}
    await callback.message.edit_text(f"<b>{esc(category)} · {TIER_LABEL[tier]}</b>\n\nsend the new price:")
    await callback.answer()


def _awaiting_setprice_input(message: Message) -> bool:
    return (
        message.text is not None
        and not message.text.startswith("/")
        and message.from_user is not None
        and message.from_user.id in settings.owner_id_set
        and message.from_user.id in _pending_price
        and "new_price" not in _pending_price[message.from_user.id]
    )


@router.message(_awaiting_setprice_input)
async def on_setprice_text(message: Message):
    pending = _pending_price[message.from_user.id]
    try:
        new_price = parse_amount(message.text.strip())
    except InvalidAmount as e:
        await message.reply(f"bad price: {e}")
        return

    category, tier = pending["category"], pending["tier"]
    async with get_session() as session:
        items = await get_items(session, category, tier)
    if not items:
        _pending_price.pop(message.from_user.id, None)
        await message.reply("nothing there at all to reprice.")
        return

    unsold = [g for g in items if g.owner_user_id is None]
    label = f"{esc(category)} · {TIER_LABEL[tier]}" if tier else esc(category)
    pending["new_price"] = new_price

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="yes, update it", callback_data="sp:confirm"),
        InlineKeyboardButton(text="cancel", callback_data="sp:cancel"),
    ]])

    if unsold:
        old_price = unsold[0].price
        await message.reply(
            f"update {len(unsold)} unsold item(s) in <b>{label}</b> from {format_amount(old_price)} to {format_amount(new_price)}?\n\n"
            "sold items keep their original price.",
            reply_markup=kb,
        )
    else:
        # Fully sold out -- there's no unsold row to hold "the current
        # price", so it has to live on the sold ones instead. This won't
        # charge or refund anyone right now, but it DOES change what
        # they'd get back if they /sellback later (see gifts.py::sell_back,
        # which falls back to a sold sibling's price when nothing's
        # unsold). That's intentional -- it's the only way to move the
        # going rate on a tier that's completely sold out.
        old_price = items[0].price
        await message.reply(
            f"<b>{label}</b> is fully sold out, nothing to sell right now.\n\n"
            f"update the going rate from {format_amount(old_price)} to {format_amount(new_price)} anyway? "
            f"this changes what current owners get back if they /sellback later, doesn't touch their balance now.",
            reply_markup=kb,
        )


@router.callback_query(F.data == "sp:confirm")
async def on_setprice_confirm(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    pending = _pending_price.pop(callback.from_user.id, None)
    if not pending or "new_price" not in pending:
        await callback.answer("session expired, run /setprice again.", show_alert=True)
        return

    category, tier, new_price = pending["category"], pending["tier"], pending["new_price"]
    async with get_session() as session:
        items = await get_items(session, category, tier)
        unsold = [g for g in items if g.owner_user_id is None]
        target = unsold if unsold else items  # fully sold out -> reprice the sold rows instead
        for g in target:
            g.price = new_price

    label = f"{esc(category)} · {TIER_LABEL[tier]}" if tier else esc(category)
    scope = "unsold" if unsold else "sold-out"
    await callback.message.edit_text(f"updated {len(target)} {scope} item(s) in {label} to {format_amount(new_price)}.")
    await callback.answer("updated.")


@router.message(Command("stock"))
async def stock_cmd(message: Message):
    """Owner-only visual stock overview -- every category, every tier,
    available/total with a quick bar so a low-stock tier is obvious at a
    glance without having to open /shop yourself."""
    if message.from_user.id not in settings.owner_id_set:
        return  # silently ignore -- no error text, so it doesn't hint the command exists

    async with get_session() as session:
        overview = await get_stock_overview(session)

    if not overview:
        await message.reply("shop's empty. /addgift to start stocking it.")
        return

    lines = [f"{pe('vip')} <b>STOCK</b>", ""]
    for cat in overview:
        lines.append(f"<b>{esc(cat['category'])}</b>")
        if cat["tiers"] is not None:
            for t in cat["tiers"]:
                bar = _stock_bar(t["available"], t["total"])
                lines.append(f"  {TIER_LABEL.get(t['tier'], t['tier'])}: {bar} {t['available']}/{t['total']} · {t['price']:,}")
        else:
            f = cat["flat"]
            bar = _stock_bar(f["available"], f["total"])
            lines.append(f"  {bar} {f['available']}/{f['total']} · {f['price']:,}")
        lines.append("")
    await message.reply("\n".join(lines))


def _stock_bar(available: int, total: int, width: int = 5) -> str:
    if total == 0:
        return ""
    filled = round((available / total) * width)
    return "🟩" * filled + "⬜" * (width - filled)


# /removegift -- owner-only teardown tool, mirrors /addgift's flow in
# reverse: category -> tier (if any) -> unsold items -> tap -> confirm.
# Only ever lists gifts nobody has bought, since a sold gift can't be
# removed (see gifts.delete_gift). No in-memory state needed here, unlike
# /addgift -- every step carries what it needs in its own callback_data.

def _rmgift_category_kb(categories: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{i}. {c['category']}", callback_data=f"rmgift:cat:{c['category']}")]
        for i, c in enumerate(categories, start=1)
    ]
    rows.append([InlineKeyboardButton(text="cancel", callback_data="rmgift:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _rmgift_categories_view(categories: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["<b>REMOVE GIFT</b>", ""]
    for i, c in enumerate(categories, start=1):
        lines.append(f"{i}. <b>{esc(c['category'])}</b>")
    lines.append("")
    lines.append("tap a category.")
    return "\n".join(lines), _rmgift_category_kb(categories)


def _rmgift_tier_kb(category: str, tiers: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{TIER_LABEL[t['tier']]} · {t['available']} unsold",
            callback_data=f"rmgift:tier:{category}:{t['tier']}",
        )]
        for t in tiers
    ]
    rows.append([InlineKeyboardButton(text="« back", callback_data="rmgift:back:categories")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _rmgift_tiers_view(category: str, tiers: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"<b>{esc(category)}</b>", ""]
    for t in tiers:
        lines.append(f"{TIER_LABEL[t['tier']]} · {t['available']}/{t['total']} unsold")
    lines.append("")
    lines.append("tap a tier.")
    return "\n".join(lines), _rmgift_tier_kb(category, tiers)


async def _rmgift_show_items(callback: CallbackQuery, category: str, tier: str | None):
    async with get_session() as session:
        items = await get_items(session, category, tier)
    unsold = [g for g in items if g.owner_user_id is None]
    label = f"{esc(category)} · {TIER_LABEL[tier]}" if tier else esc(category)
    back_cb = f"rmgift:back:tier:{category}" if tier else "rmgift:back:categories"

    if not unsold:
        text = f"<b>{label}</b>\n\nnothing unsold here to remove."
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« back", callback_data=back_cb)]])
        await callback.message.edit_text(text, reply_markup=kb)
        return

    lines = [f"<b>{label}</b>", "", "only unsold items can be removed:"]
    for i, g in enumerate(unsold, start=1):
        lines.append(f"{i}. {raw_tag(g.emoji_id)} · {format_amount(g.price)} aura")
    lines.append("")
    lines.append("tap one to delete it.")

    buttons = [InlineKeyboardButton(text=str(i), callback_data=f"rmgift:item:{g.id}") for i, g in enumerate(unsold, start=1)]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    rows.append([InlineKeyboardButton(text="« back", callback_data=back_cb)])
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("removegift"))
async def removegift_cmd(message: Message):
    """Owner-only. Walks category -> tier -> unsold item -> confirm, then
    permanently deletes that single gift row. Can't touch anything already
    bought."""
    if message.from_user.id not in settings.owner_id_set:
        return  # silently ignore -- no error text, so it doesn't hint the command exists

    async with get_session() as session:
        categories = await get_categories(session)
    if not categories:
        await message.reply("shop's empty, nothing to remove.")
        return

    text, kb = _rmgift_categories_view(categories)
    await message.reply(text, reply_markup=kb)


@router.callback_query(F.data == "rmgift:cancel")
async def on_rmgift_cancel(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    await callback.message.edit_text("cancelled, nothing removed.")
    await callback.answer()


@router.callback_query(F.data == "rmgift:back:categories")
async def on_rmgift_back_categories(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    async with get_session() as session:
        categories = await get_categories(session)
    if not categories:
        await callback.message.edit_text("shop's empty, nothing to remove.")
    else:
        text, kb = _rmgift_categories_view(categories)
        await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("rmgift:cat:"))
async def on_rmgift_category(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    category = callback.data.split(":", 2)[2]
    async with get_session() as session:
        categories = await get_categories(session)
        meta = next((c for c in categories if c["category"] == category), None)
        has_tiers = bool(meta and meta["has_tiers"])
        tiers = await get_tiers(session, category) if has_tiers else []

    if has_tiers:
        text, kb = _rmgift_tiers_view(category, tiers)
        await callback.message.edit_text(text, reply_markup=kb)
    else:
        await _rmgift_show_items(callback, category, None)
    await callback.answer()


@router.callback_query(F.data.startswith("rmgift:back:tier:"))
async def on_rmgift_back_tier(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    category = callback.data.split(":", 3)[3]
    async with get_session() as session:
        tiers = await get_tiers(session, category)
    text, kb = _rmgift_tiers_view(category, tiers)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("rmgift:tier:"))
async def on_rmgift_tier(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    _, _, category, tier = callback.data.split(":", 3)
    await _rmgift_show_items(callback, category, tier)
    await callback.answer()


@router.callback_query(F.data.startswith("rmgift:item:"))
async def on_rmgift_item(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    gift_id = int(callback.data.split(":", 2)[2])
    async with get_session() as session:
        gift = await get_gift(session, gift_id)

    if gift is None:
        await callback.answer("that one's already gone.", show_alert=True)
        return
    if gift.owner_user_id is not None:
        await callback.answer("that one's been sold, can't remove it.", show_alert=True)
        return

    text = (
        f"delete {raw_tag(gift.emoji_id)} · {format_amount(gift.price)} aura "
        f"from <b>{esc(gift.category)}</b>?\n\nthis can't be undone."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="yes, delete it", callback_data=f"rmgift:confirm:{gift_id}"),
        InlineKeyboardButton(text="cancel", callback_data="rmgift:cancel"),
    ]])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("rmgift:confirm:"))
async def on_rmgift_confirm(callback: CallbackQuery):
    if callback.from_user.id not in settings.owner_id_set:
        await callback.answer()
        return
    gift_id = int(callback.data.split(":", 2)[2])
    async with get_session() as session:
        gift = await delete_gift(session, gift_id)

    if gift is None:
        await callback.message.edit_text("nothing removed, that gift's no longer there to delete.")
        await callback.answer()
        return

    await callback.message.edit_text(
        f"removed {raw_tag(gift.emoji_id)} · {format_amount(gift.price)} aura from {esc(gift.category)}."
    )
    await callback.answer("deleted.")
