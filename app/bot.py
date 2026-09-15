import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from app.config import settings, ECONOMY
from app.database.db import init_db, get_session
from app.handlers import economy, wallet, social, rps, coin, dice, highlow, dart, shop, pvp_common, admin, inbox, streak
from app.services.challenge import cancel_expired

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aurabot")


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(wallet.router)
    dp.include_router(economy.router)
    dp.include_router(streak.router)
    dp.include_router(social.router)
    dp.include_router(rps.router)
    dp.include_router(coin.router)
    dp.include_router(dice.router)
    dp.include_router(highlow.router)
    dp.include_router(dart.router)
    dp.include_router(shop.router)
    dp.include_router(admin.router)
    dp.include_router(pvp_common.router)  # owns "acc:", "vsbot:", "coin:" callbacks for all games
    dp.include_router(inbox.router)  # DM-only: /bal, /stats, /gtop, banner /start, fallback for everything else
    return dp


async def _challenge_sweep_loop() -> None:
    """Bug fix: hosted PvP challenges (rps/coin/dice/highlow) reserve the
    creator's wager immediately but only got refunded if someone happened to
    try accepting them after expiry -- if nobody ever did, the reservation
    sat stuck forever. `/bal` shows the raw total balance, so a player could
    see they "had" the aura while every wager check (balance - reserved) kept
    rejecting them, with no visible reason why.

    This loop is the actual fix: every CHALLENGE_SWEEP_INTERVAL_S seconds,
    sweep for expired pending challenges and refund them automatically."""
    while True:
        try:
            async with get_session() as session:
                expired = await cancel_expired(session)
                if expired:
                    logger.info(f"challenge sweep: refunded {len(expired)} expired challenge(s)")
        except Exception:
            logger.exception("challenge sweep failed")
        await asyncio.sleep(ECONOMY.CHALLENGE_SWEEP_INTERVAL_S)


async def _run_health_server() -> None:
    """Render's free tier is Web Service only — Background Workers have no
    free instance type. Running a bot with long-polling doesn't need an
    inbound port at all, but binding one anyway is what makes Render treat
    this as a (free) Web Service instead of a (paid, $7/mo+) Background
    Worker. Same trick already used for PozzCapital's Render deploy.

    Note: Render's free Web Services still spin down after 15 minutes with
    no INBOUND HTTP traffic — the bot's own outbound polling to Telegram
    doesn't count. Point an uptime pinger (e.g. UptimeRobot, free) at this
    service's URL every 5-10 minutes to keep it awake 24/7."""
    app = web.Application()
    app.router.add_get("/", lambda request: web.Response(text="aurabot is running"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"health check server listening on port {port}")


async def run() -> None:
    await init_db()
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()
    await _run_health_server()
    # BUG FIX: asyncio only holds a WEAK reference to a task once you drop
    # the return value of create_task -- nothing here was keeping this loop
    # alive. Under the wrong GC timing it could get silently collected mid-
    # run: no crash, no log line, it just stops existing. This is the
    # documented asyncio gotcha ("save a reference to the result"). Keeping
    # it on the bot object is enough to prevent that.
    bot.sweep_task = asyncio.create_task(_challenge_sweep_loop())
    logger.info("aurabot starting polling")
    await dp.start_polling(bot)
