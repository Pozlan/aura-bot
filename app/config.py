"""
All tunable economy values live here. Nothing gameplay-related should be
hardcoded inside handlers/games/services — pull it from here so balancing
the game never means hunting through source files.

Per-group overrides (via /gconfig) are stored in GroupSettings and layered
on top of these defaults at read time — see services/economy.py:get_group_config.
"""
from dataclasses import dataclass, field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./aurabot.db"
    owner_ids: str = ""

    class Config:
        env_file = ".env"

    @property
    def owner_id_set(self) -> set[int]:
        return {int(x) for x in self.owner_ids.split(",") if x.strip()}


settings = Settings()


@dataclass(frozen=True)
class EconomyConfig:
    # /farm
    # Bumped 50x (was 800-3200) -- launch-day numbers that stopped mattering
    # once the group's balances settled in the millions. Same cooldown, same
    # odds, just an amount worth actually running the command for.
    FARM_MIN: int = 40_000
    FARM_MAX: int = 160_000
    FARM_COOLDOWN_S: int = 24 * 3600

    # /work
    # All ranges x50 for the same reason as /farm above.
    WORK_COOLDOWN_S: int = 3 * 3600
    WORK_JOBS: dict = field(default_factory=lambda: {
        "cleaner": (7_500, 30_000),
        "delivery driver": (10_000, 37_500),
        "freelancer": (12_500, 60_000),
        "mechanic": (15_000, 45_000),
        "developer": (20_000, 80_000),
        "chef": (12_500, 42_500),
        "driver": (10_000, 35_000),
        "security guard": (10_000, 32_500),
        "trader": (5_000, 100_000),
        "construction worker": (15_000, 47_500),
    })

    # /loot
    LOOT_COOLDOWN_S: int = 2 * 3600
    LOOT_SUCCESS_RATE: float = 0.55
    LOOT_MIN: int = 50_000
    LOOT_MAX: int = 500_000

    # /hunt
    HUNT_COOLDOWN_S: int = 4 * 3600
    HUNT_SUCCESS_RATE: float = 0.5
    HUNT_MIN_STAKE: int = 200
    HUNT_MAX_STAKE: int = 10_000_000       # was uncapped -- let a big enough stake x4 reward snowball a balance
    HUNT_REWARD_MULT: tuple = (1.5, 4.0)   # win: stake * random in this range
    HUNT_LOSS_MULT: tuple = (0.5, 1.0)     # loss: stake * random in this range, deducted

    # /luck
    LUCK_COOLDOWN_S: int = 24 * 3600
    # /luck is gains-only (see handlers/economy.py::luck) -- three tiers:
    # rare zero, common medium, rare big. No loss branch at all.
    LUCK_ZERO_RATE: float = 0.10   # rare: nothing this time
    LUCK_BIG_RATE: float = 0.15    # rare: big win (remainder, 0.75, is the common medium tier)
    LUCK_MEDIUM_MIN: int = 100_000
    LUCK_MEDIUM_MAX: int = 500_000
    LUCK_BIG_MIN: int = 1_000_000
    LUCK_BIG_MAX: int = 3_000_000

    # House wager caps (0 = no cap -- unlimited wager allowed vs house)
    RPS_MAX_HOUSE_WAGER: int = 250_000
    COIN_MAX_HOUSE_WAGER: int = 250_000
    DICE_MAX_HOUSE_WAGER: int = 250_000
    # HighLow (solo one-shot game vs house)
    HIGHLOW_MAX_HOUSE_WAGER: int = 250_000
    HIGHLOW_MAX_ROUNDS: int = 15      # unused now that HighLow is one-shot, kept in case a streak mode returns
    DART_MAX_WAGER: int = 5_000_000
    BJ_MAX_HOUSE_WAGER: int = 250_000
    SLOTS_MAX_WAGER: int = 250_000

    # /shop buyback -- selling an owned gift back to the shop, not to
    # another player. Refunds 80% of price, the gift resets to unowned and
    # goes back into stock at its original price. The 20% cut is what
    # keeps this from being a free round-trip -- buy then sell back always
    # costs the player something, so there's no way to profit off it.
    GIFT_REFUND_RATE: float = 0.8

    # PvP challenges
    CHALLENGE_EXPIRATION_S: int = 3 * 60
    CHALLENGE_SWEEP_INTERVAL_S: int = 30  # how often the background task checks for expired challenges to refund

    # Robbery
    # No per-robber cooldown -- removed. Every throttle now lives on the
    # victim's side instead (grace period + hit cap below), not the
    # attacker's, so robbing is instant/free to attempt.
    # No more random success roll -- an unprotected target is always
    # robbable (protection is the only defense, not luck). Steal % is
    # randomized per-hit instead of a flat cut.
    ROBBERY_STEAL_PCT_MIN: float = 0.15
    ROBBERY_STEAL_PCT_MAX: float = 0.20
    ROBBERY_MIN_TARGET_BALANCE: int = 5000
    # No failure penalty -- a failed robbery costs the robber nothing.
    # Steal is currently flat 20% on success (was a random 2-15% range
    # with a 5% self-penalty on failure).

    # Victim grace period: after getting successfully robbed, NOBODY can
    # rob that person again for this long -- not just the same robber.
    # Automatic, no player action needed. Separate from /protect (player-
    # activated, 24h, has a door mechanic).
    ROBBERY_VICTIM_GRACE_S: int = 10 * 60

    # Hard hit cap: once a player's been successfully robbed this many
    # times since their last /protect activation, they're fully un-
    # robbable (regardless of grace period) until they run /protect
    # again. The counter resets on every /protect activation. Combined
    # with removing the robber cooldown above, this is what actually
    # bounds total exposure -- no cooldown slows attackers down, so the
    # cap has to live here instead.
    ROBBERY_MAX_HITS_BEFORE_PROTECTION: int = 2

    # Protection
    PROTECTION_DURATION_S: int = 24 * 3600
    DOOR_DURATION_S: int = 5 * 60

    # Starting balance for new players
    STARTING_BALANCE: int = 100_000

    # /streak
    # Rolling window, not calendar day -- must run /streak again within
    # this many seconds of the last activation or the streak breaks. Below
    # that (i.e. < 24h since last activation) it's just "already claimed
    # today, come back later," same shape as /farm's cooldown.
    STREAK_WINDOW_S: int = 24 * 3600
    # Hard deadline: activating at or after this many seconds since the
    # last activation breaks the streak (resets to 1) instead of
    # continuing it. Two bounds are required, not one -- STREAK_WINDOW_S
    # alone only rejects activating too SOON (spam prevention); without
    # this upper bound there is no way to ever activate too LATE, so a
    # streak could never actually break. Set to 2x the window: the first
    # 24h are the "already claimed" cooldown, the next 24h are the grace
    # period to come back before it's gone.
    STREAK_BREAK_S: int = 2 * 24 * 3600

    # day-count -> emoji ID of the badge minted on reaching that day count.
    # These are milestones, not stock: every player who reaches a given
    # count gets their OWN fresh Gift row with this emoji (see
    # services/streak.py::mint_streak_gift), not a shared one-of-one item.
    # Lifetime-earned -- once claimed, a broken streak climbing back
    # through the same number does not re-grant it (see
    # PlayerState.streak_milestone_claimed).
    STREAK_MILESTONES: dict = field(default_factory=lambda: {
        3: "5447640143475261975",
        10: "5447656636149681563",
        15: "5447256654435337586",
        20: "5447591434251158839",
        30: "5447644863644320013",
        50: "5438571934210082705",
        60: "5447236223275910637",
        90: "5458475383890394206",
        120: "5447367030799877537",
        150: "5447516298093282460",
        180: "5447246917744478110",
        210: "5458640241915084025",
        300: "5447550357183939181",
    })


ECONOMY = EconomyConfig()
