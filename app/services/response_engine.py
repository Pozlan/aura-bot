"""
Spec section 37: game logic emits structured events, this module turns
them into varied, contextual text. Nothing in games/ or handlers/ should
contain a hardcoded personality string -- if you're tempted to write
f"you won {amount} aura" inline, it belongs in POOLS instead.

Usage:
    from app.services.response_engine import react
    line = react("normal_win", amount=50_000)

`react` never returns the same string twice in a row for the same
category (best-effort, process-local -- fine for a single bot instance).

POOLS is built with pe(...) calls (see premium_emoji.py) instead of
plain unicode emoji wherever a custom emoji ID has been provided for
that category. This only renders correctly because the bot runs in HTML
parse_mode -- see app/bot.py.
"""
import random
from app.services.economy import format_amount, classify_amount
from app.services.premium_emoji import pe

_last_used: dict[str, str] = {}

POOLS: dict[str, list[str]] = {
    # --- win/loss by size ---
    "small_win": [f"{pe('up')} easy money.", f"{pe('up')} small but it counts.", f"{pe('up')} free aura.", f"{pe('up')} nice, take it."],
    "normal_win": [f"{pe('gg')} clean.", f"{pe('gg')} solid win.", f"{pe('gg')} clean work.", f"{pe('gg')} that's a W."],
    "big_win": [f"{pe('crit')} {{amount}} secured. disgusting.", f"{pe('crit')} that's a real payday.", f"{pe('crit')} big money move.", f"{pe('crit')} he's eating good."],
    "massive_win": [f"{pe('boom')} <b>WHAT.</b>", f"{pe('boom')} screenshot this immediately.", f"{pe('boom')} someone check the logs.", f"{pe('boom')} that's insane."],

    "small_loss": [f"{pe('lol')} barely felt that.", f"{pe('lol')} rounding error.", f"{pe('lol')} meh, next one."],
    "normal_loss": [f"{pe('rip')} that's rough.", f"{pe('rip')} ouch.", f"{pe('rip')} unlucky.", f"{pe('rip')} it happens."],
    "big_loss": [f"{pe('rage')} that hurt to watch.", f"{pe('rage')} {{amount}} gone in one click.", f"{pe('rage')} painful.", f"{pe('rage')} brutal."],
    "massive_loss": [f"{pe('ko')} bro just disappeared.", f"{pe('ko')} {{amount}} gone.", f"{pe('ko')} someone check on him.", f"{pe('ko')} that's a career-ender."],

    # --- streaks ---
    "winning_streak": [
        f"{pe('buff')} {{streak}} wins straight. somebody stop him.",
        f"{pe('buff')} {{name}} is on a <b>{{streak}} win streak</b>. someone needs to stop this guy.",
        f"{pe('buff')} {{streak}} in a row now. this is getting unfair.",
    ],
    "losing_streak": [
        f"{pe('bg')} {{losses}}W / {{wins}}L... this isn't your game.",
        f"{pe('bg')} {{wins}}W / {{losses}}L. maybe try something else.",
        f"{pe('bg')} another one. rough stretch.",
    ],
    "streak_ended": [
        f"{pe('wtf')} <b>THE STREAK IS OVER</b>. {{streak}} straight wins. {{opponent}} finally did it.",
        f"{pe('wtf')} the run ends here. {{streak}} wins, gone.",
    ],
    "comeback": [
        f"{pe('res')} was down a few games ago btw.",
        f"{pe('res')} comeback of the century.",
        f"{pe('res')} he actually survived.",
    ],

    # --- closeness ---
    "close_win": [f"{pe('ns')} won by <b>1 point</b>", f"{pe('ns')} that was way too close.", f"{pe('ns')} barely made it."],
    "close_loss": [f"{pe('sad')} so close. painful.", f"{pe('sad')} right at the edge. brutal."],
    "draw": ["draw. wagers returned.", "dead even.", "nobody wins this one."],

    # --- wager size framing (shown before the game resolves) ---
    "huge_wager": [f"{pe('hype')} {{amount}}. you really wanna do this?", f"{pe('hype')} that's a big number to risk.", f"{pe('hype')} bold. respect it."],
    "tiny_wager": [f"{pe('noob')} {{amount}}? be serious.", f"{pe('noob')} that's barely a wager.", f"{pe('noob')} why even bother, lol."],

    # --- robbery ---
    "rob_success": [
        # no premium ID provided for this one yet -- send /emojiid on a
        # "hack"-style icon if you want to add it, plain 🥷 until then
        "🥷 <b>ROBBED</b>\n{robber} stole <b>{amount}</b> from {target}.",
        "🥷 clean hit. {amount} gone from {target}.",
    ],
    "rob_failure": [
        f"{pe('ban')} <b>ROBBERY FAILED</b>\n{{target}} saw you coming. nothing gained.",
        f"{pe('ban')} caught red-handed. no aura lost, but no aura gained either.",
    ],
    "robbed": [f"{pe('hit')} you just got hit. {{amount}} gone.", f"{pe('hit')} someone got to you first."],
    "protection": [f"{pe('save')} <b>ROBBERY BLOCKED</b>\n{{target}} is protected. find someone else."],

    # --- house games ---
    "house_win": [f"{pe('ez')} House just got cooked.", f"{pe('ez')} the house didn't see that coming.", f"{pe('ez')} took it clean off the house."],
    "house_loss": ["the house wins this one.", "house takes it.", "better luck next time."],

    # --- special ---
    "jackpot": ["🎰 <b>JACKPOT</b>\nthe house just got robbed.", "🎰 <b>777</b>\nWHAT"],
    "blackjack": [f"{pe('pog')} <b>BLACKJACK</b>\nyeah, that's disgusting.", f"{pe('pog')} natural 21. clean."],
    "bust": [f"{pe('ko')} <b>BUST</b>\n{{over}} points over.", f"{pe('ko')} too greedy. busted."],
}


def _select(category: str, pool: list[str]) -> str:
    if len(pool) == 1:
        return pool[0]
    choices = [c for c in pool if c != _last_used.get(category)]
    choice = random.choice(choices or pool)
    _last_used[category] = choice
    return choice


def react(category: str, **context) -> str:
    pool = POOLS.get(category)
    if not pool:
        return ""
    template = _select(category, pool)
    ctx = dict(context)
    if "amount" in ctx and isinstance(ctx["amount"], int):
        ctx["amount"] = format_amount(ctx["amount"])
    try:
        return template.format(**ctx)
    except KeyError:
        # a template needed a var the caller didn't pass -- fail soft, not loud
        return template


def win_category(amount: int) -> str:
    return {"small": "small_win", "normal": "normal_win", "big": "big_win", "massive": "massive_win"}[
        classify_amount(amount)
    ]


def loss_category(amount: int) -> str:
    return {"small": "small_loss", "normal": "normal_loss", "big": "big_loss", "massive": "massive_loss"}[
        classify_amount(amount)
    ]


def wager_framing(amount: int, house_cap: int | None) -> str | None:
    """Returns a pre-game reaction line if the wager is notably large or tiny, else None."""
    if amount <= 200:
        return react("tiny_wager", amount=amount)
    if amount >= 5_000_000 or (house_cap and amount >= house_cap):
        return react("huge_wager", amount=amount)
    return None
