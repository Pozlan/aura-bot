"""Tests never need a real bot token — set a dummy one before anything
under app/ imports Settings(), so pydantic-settings doesn't blow up."""
import os

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
