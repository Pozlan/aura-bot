"""
Every stored and compared datetime in this app is naive UTC — no tzinfo.

Why: SQLite silently drops tzinfo on round-trip (store an aware datetime,
read it back naive), which makes `stored_time < datetime.now(timezone.utc)`
raise TypeError. That crash was happening inside challenge acceptance and
protection checks — the callback handler swallowed it, so the bot just
looked broken with no visible error.

Fix: never create an aware datetime anywhere in this app. Always call
utcnow() from here. Postgres in production is fine with naive UTC as long
as every write and read agrees on the convention — which this enforces.
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
