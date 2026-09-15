# aurabot — Telegram Social Economy Game Bot

Phase 1 + Phase 2 of the build described in the spec, plus a real bug fix
from live testing (see below — worth reading, it'll bite you again if you
add a new datetime field without following the convention it describes).

## What's working right now

- `/start` (short welcome) + `/help` (full command list), `/bal`, `/top`, `/stats`
- `/farm`, `/work` (interactive — pick a job from buttons), `/loot`,
  `/hunt`, `/luck` — full earning loop. Reward ranges and cooldowns live
  in `app/config.py::EconomyConfig` — that's the single source of truth,
  intentionally not duplicated here as literal numbers so this doc can't
  go stale every time balancing changes.
- `/tip`, `/rob` (both reply-based), `/protect` + Open door button
- `/dart` — solo vs-house, one throw, uses Telegram's real animated dart
  emoji. Tuned to zero expected value (push/lose/win/bullseye).
- `/rps`, `/coin`, `/dice` — PvP challenge flow (create → accept →
  resolve, RPS adds a private simultaneous-choice step) **and** Play vs
  Bot, house wager cap set in config. Dice uses Telegram's native
  animated dice (`send_dice`) for both modes, not a silently-picked number.
- `/highlow` — one-shot higher/lower vs house. First card is always
  2-12 so both directions are always legal to guess; the second card is
  guaranteed to never equal the first, so there's no tie/push case. One
  guess, immediate resolution, payout scales with how unlikely the guess
  was.
- `/cancel` — public self-service: bail on your own hosted PvP challenge
  before anyone accepts it.
- **Shop / gift economy** (`handlers/shop.py`, `services/gifts.py`) —
  unique, one-of-one collectible gifts bought with aura. `/shop` walks
  category → tier (if the category has one) → numbered items, all
  button-driven. `/sellback` refunds a % of price and returns the gift to
  stock. `/equip` picks which owned gift shows as your badge next to your
  name in `/stats`, `/bal`, `/top`. Owner-only: `/addgift` (button flow or
  pipe-syntax restock) and `/removegift` (delete unsold stock only —
  never touches anything already bought).
- **DM support** (`handlers/inbox.py`) — scoped on purpose: DMs are for
  checking in (`/bal`, `/stats`, `/gtop`), not for playing. Games,
  farming, tipping, robbing etc. all stay group-only.
- **Owner tools** (`handlers/admin.py`) — `/grant`, `/deduct` (both
  reply-based, silent to everyone else), `/reset` (force-refund every
  open challenge bot-wide), `/reconcile` (fix stuck reserved balances),
  `/emojiid` (extract a custom emoji's ID for wiring into game messages).
  Every owner action still writes to the Transaction ledger like any
  normal balance change, so it's auditable.
- Group-scoped economy: every balance/stat is keyed to `(user, group)`
- Atomic, race-safe balance mutations with an append-only transaction ledger
- A real response engine (`app/services/response_engine.py`) driving
  contextual, varied text instead of hardcoded strings
- `/stats` shows total lifetime aura wagered (RPS/Coin/Dice/HighLow only —
  `/hunt` doesn't share the same reservation mechanism this is tracked
  through, so it isn't counted; flag if you want that folded in)
- 76 passing tests, including regression tests for the datetime bug below
  (may be higher now — check `tests/` for the current count)

## What's not built yet (by design, see roadmap below)

Blackjack, Slots (config already has wager-cap placeholders for both —
`BJ_MAX_HOUSE_WAGER`, `SLOTS_MAX_WAGER` — but no handler exists yet),
`/gconfig`, `/gstats`, challenge-expiry background sweep, Alembic
migrations.

## A real bug that was caught during manual testing — read this

**Symptom:** `/rps` accept did nothing, `/protect` did nothing, and
repeat-cooldown messages ("already claimed") never showed — all with no
visible error.

**Cause:** SQLite silently drops timezone info when a datetime round-trips
through it. Every comparison in the code was built as
`datetime.now(timezone.utc)` (timezone-aware) compared against a value
just read back from the database (timezone-naive after the round-trip).
Comparing an aware datetime to a naive one raises `TypeError` — and that
exception was happening inside Telegram callback handlers, where aiogram
logs it and moves on. From the user's side, the button just... didn't do
anything.

**Why the original tests didn't catch it:** they reused one SQLAlchemy
session for create + read, so the ORM's identity map returned the exact
same in-memory Python object instead of re-querying the database — tzinfo
was never actually lost in that path. Every real Telegram command opens a
**fresh** session per request, which always re-reads from disk and always
hit this. The fix added regression tests (`test_datetime_regression.py`,
plus one in `test_challenge_flow.py`) that deliberately open fresh
sessions to catch this class of bug again.

**Fix:** `app/utils/time.py` is now the single source of truth for "now" —
`utcnow()` always returns naive UTC, and every DB column that stores a
datetime is a plain `DateTime()` (no `timezone=True`). **If you add a new
datetime field or comparison anywhere in this codebase, import `utcnow`
from `app.utils.time` — never call `datetime.now(timezone.utc)` directly,**
or this bug comes back.

## Architecture

```
app/
  config.py            # Settings (env) + EconomyConfig (all tunable numbers)
  database/
    models.py           # SQLAlchemy models — group-scoped PlayerState is the core table
    db.py                # async engine/session, init_db()
  services/
    economy.py           # amount parsing, atomic balance ops, formatting
    cooldown.py          # generic per-(user, group, action) cooldown tracker
    challenge.py          # reusable PvP challenge create/accept/resolve/expire
    response_engine.py    # the personality system — every game reacts through this
  games/
    rps.py                # pure game logic, no I/O — template for future engines
  handlers/
    wallet.py, economy.py, social.py, rps.py, coin.py, dice.py,
    highlow.py, dart.py, pvp_common.py, shop.py, admin.py, inbox.py
  utils/
    keyboards.py, targeting.py
tests/
```

**Why it's split this way:** game logic never touches Telegram objects or
a DB session directly — handlers own I/O, services own rules, games own
pure mechanics. Adding Coin Flip means writing `app/games/coin.py` (pure
logic) and `app/handlers/coin.py` (mirrors `rps.py`'s shape) — the
challenge system, economy, cooldowns, and response engine are already
built and don't change.

## Database schema (current)

- **User** — global Telegram identity, no balance
- **Group** — a Telegram chat
- **GroupSettings** — admin overrides (not yet wired to `/gconfig`)
- **PlayerState** — the wallet: `balance`, `reserved` (locked in open
  challenges), streaks, win/loss counts, protection timestamps. Unique per
  `(user_id, group_id)` — this is what makes each group's economy independent.
- **Cooldown** — one row per `(user, group, action)`
- **Challenge** — a pending/accepted/resolved/expired PvP challenge,
  `game` field discriminates which engine resolves it
- **Transaction** — append-only ledger, every balance change ever made
- **GameHistory** — one row per completed game, feeds `/stats`, `/gstats`,
  and future response-engine context (recent games, opponent history)
- **Gift** — a unique, one-of-one shop item: category, tier (nullable —
  Limited Edition items skip tiers), price, emoji id, and
  `owner_user_id` (null = unsold, in stock). Deleting a row is only ever
  allowed while `owner_user_id` is null — see `services/gifts.py::delete_gift`.

## Transaction model

`adjust_balance()` in `economy.py` is the *only* function allowed to touch
`PlayerState.balance`. It refuses negative balances and writes a
`Transaction` row on every call — so the ledger is always reconstructable
and auditable.

PvP wagers don't get deducted at challenge-creation time — they get
**reserved** (`PlayerState.reserved`) so the balance keeps existing, just
minus what's locked up. `available_balance()` = `balance - reserved`.
This is what stops a player from creating five simultaneous challenges
with aura they don't actually have, and what makes challenge expiry a
clean no-op (release the reservation, nothing else changes).

## How PvP challenges work

1. `/rps 250k` → `create_challenge()` reserves the wager and inserts a
   `Challenge(status="pending")`. Message posted with **Accept** +
   (conditionally) **Play vs Bot** buttons.
2. Someone taps **Accept** → `accept_challenge()` validates (not expired,
   not the creator, has enough available balance), reserves their wager
   too, flips status to `accepted`.
3. Both players privately tap Rock/Paper/Scissors. The callback handler
   stores each choice in `Challenge.state` (JSON) and only resolves once
   both are present — so neither player ever sees the other's pick early.
4. `resolve_challenge()` releases both reservations and moves aura from
   loser to winner in one DB transaction — draws just release both
   reservations with no transfer.

Every step re-validates against the DB, never trusts callback data blindly
(spec section 34) — challenge existence, expiry, status, and identity are
all checked server-side on every callback.

## How protection works

`/protect` sets `protected_until` 24h out. **Open door · 5m** sets
`door_open_until` 5 minutes out and doesn't touch `protected_until` — the
robbery handler (phase 3) will check `door_open_until` first: if it's
active, the player is robbable regardless of `protected_until`. Both
timestamps live in the DB, not memory, so they survive a bot restart.

## How the response engine works

Games never write reaction strings inline. They emit a category
(`normal_win`, `massive_loss`, `winning_streak`, ...) with context
(`amount`, `streak`, names), and `react()` in `response_engine.py` picks a
template from that category's pool, avoiding immediate repeats. Adding
personality later means editing `POOLS`, not touching game logic —
exactly the separation spec section 37 asks for.

## Economy configuration

Every tunable number (farm reward range, cooldowns, house wager caps,
robbery rates, protection duration...) lives in `EconomyConfig` in
`app/config.py`. Nothing is scattered through handlers.

## Setup — local development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: paste your BOT_TOKEN from @BotFather
# DATABASE_URL default (sqlite) is fine for local dev

python main.py
```

## Deployment — Render

**Correction from earlier advice:** Render's Background Worker service type
has no free tier — it's a paid instance starting at $7/month. Only Web
Services have a free tier, so this bot binds a tiny HTTP health-check
server (`app/bot.py`'s `_run_health_server`) purely so Render treats it as
a Web Service. The bot itself still just long-polls Telegram; the HTTP
server doesn't do anything except satisfy Render's port requirement —
same workaround already used for PozzCapital's Render deploy.

1. New **Web Service** (not Background Worker) from this repo.
2. Build command: `pip install -r requirements.txt`
3. Start command: `python main.py`
4. Environment variables: `BOT_TOKEN`, `OWNER_IDS` (your Telegram user ID,
   comma-separated if more than one), `DATABASE_URL` (point this at a
   Render Postgres instance — **do not use the SQLite default in
   production**, Render's filesystem is ephemeral and you'll lose every
   balance on redeploy).
5. Same Python-version pinning lesson from PozzCapital applies — pin to
   3.11.x in `runtime.txt` if Render defaults to something newer than
   this stack has been tested against.
6. **Free-tier catch:** Render's free Web Services spin down after 15
   minutes with no *inbound* HTTP traffic. The bot's own polling to
   Telegram is outbound, so it doesn't count and won't keep the service
   awake on its own. Set up a free uptime pinger (e.g. UptimeRobot) to
   hit the service's URL every 5-10 minutes if you want it always-on
   without paying for the Starter tier.

## Button colors

Telegram doesn't let bots set inline button colors — that's controlled
entirely by the Telegram client's theme, not exposed in the Bot API.
The Higher/Lower buttons use 🟩/🟥 emoji to approximate it visually since
actual color isn't available through this API. A genuinely custom-styled
UI (real colored buttons, like some bots achieve) requires building a
Telegram Mini App — a hosted web page opened inside Telegram via a
WebApp button — which is a materially bigger build than anything here
(hosting, a web frontend, a different integration model). Not attempted
in this phase; flag if you want to pursue it, it's a separate project
more than a feature addition.

## Telegram button labels can't render any formatting — not HTML, not custom emoji

A real bug from live testing: `format_amount()` (which includes a
`<tg-emoji>` tag) got used inside an `InlineKeyboardButton` label, and
Telegram rendered the raw tag text on the button instead of the emoji —
`Accept · 1,000 <tg-emoji emoji-id="...">...</tg-emoji>`. Button labels
are **plain text only**, no exceptions — no `<b>`, no `<tg-emoji>`, no
markdown. If you're tempted to reuse a formatted string on a button
anywhere, don't; write the button's own plain label instead. The `Accept`
button now just says "Accept" — the amount is already visible in the
message text above it.

## Premium (custom) emoji — now live

Wired throughout the bot using the 33 IDs pulled from the "Game Emoji"
pack via `/emojiid`. See `app/services/premium_emoji.py` for the full
key → ID mapping and `pe(key)` helper — e.g. `pe("top")` returns the
inline `<tg-emoji>` tag for the trophy icon.

Getting an ID still works the same way: `/emojiid` (owner-only, silent
to everyone else) — reply to a message containing a custom emoji, or
send one right after the command, and it replies with the ID.

Actually wiring an ID into the bot's messages required a real
infrastructure change, not just dropping IDs into strings:

- **Parse mode switched from legacy Markdown to HTML.** Custom emoji only
  render via the `<tg-emoji emoji-id="...">` tag, and there's no Markdown
  equivalent. Every `**bold**` across the codebase was mechanically
  converted to `<b>bold</b>` — verified afterward that nothing was missed
  or double-converted (`grep` for stray `**` should only ever show
  Python's `10**15` and `**kwargs`, nothing else).
- **Every literal `<amount>` placeholder in usage strings was a landmine.**
  Under HTML parse mode, a usage string like `/rps <amount>` looks like a
  malformed tag to Telegram's parser and breaks the message entirely.
  Every instance across the game handlers is now escaped to
  `&lt;amount&gt;`.
- **Every Telegram display name is now HTML-escaped before going into a
  message** (`app/utils/html_esc.py`, applied via `esc()`). Without this,
  a display name containing `<`, `>`, or `&` could break message parsing
  or inject fake formatting into the bot's own output — tested directly
  with a hostile-ish name to confirm it degrades safely instead of
  breaking.
- One gap, flagged rather than guessed at: no ID was given for a
  "successful robbery" icon (the "HACK" icon from the first pack wasn't
  in the second batch of IDs). `rob_success` still uses a plain 🥷 until
  that ID is provided.

If you add a new message anywhere with a raw `<`, `>`, `&`, or
`**bold**`, it needs the same treatment: HTML-escape any dynamic content,
use `<b>`/`<i>`/`<code>` instead of Markdown syntax, and route any new
emoji through `pe()` rather than a literal `<tg-emoji>` string.

## Tests

```bash
pytest -q
```

76 tests, all passing. Covers: amount parsing edge cases (including the
k/m/b/t suffixes and comma-separated display), response engine category
selection, every game's win/loss/draw resolution, the challenge lifecycle
under adversarial conditions (self-accept, double-accept, double-spend
via reservation), the shared PvP/House finalize helpers, the one-shot
HighLow lifecycle (reservation, win/loss payout, no-tie guarantee,
illegal-guess rejection, acting on a finished run), robbery's
protection/door-open interaction, custom emoji ID extraction,
HTML-escaping of hostile display names, premium emoji tag generation,
the total-wagered tracking, the live-database column migration, and the
datetime regression class described above.

## Roadmap (phase 3)

`/gconfig` + `/gstats`, Blackjack, Slots, background sweep for expired
challenges (currently only checked lazily on accept — fine for now, but
a stale pending challenge won't refund itself until someone tries to
accept it), Alembic migrations, expanded response pools per spec
section 38 ("create a significantly larger response library").
