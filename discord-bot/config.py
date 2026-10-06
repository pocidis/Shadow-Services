"""Configuration loaded from environment variables / .env file."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _id_list(raw: str) -> list[int]:
    """Parse a comma/semicolon separated list of Discord IDs."""
    out: list[int] = []
    for chunk in (raw or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            out.append(int(chunk))
    return out


def _colour(raw: str | None, fallback: int) -> int:
    if not raw:
        return fallback
    raw = raw.strip().lstrip("#")
    try:
        return int(raw, 16)
    except ValueError:
        return fallback


class Config:
    def __init__(self) -> None:
        self.token: str = os.getenv("DISCORD_TOKEN", "").strip()
        self.prefix: str = os.getenv("PREFIX", "!").strip() or "!"
        self.owner_ids: list[int] = _id_list(os.getenv("OWNER_IDS", ""))

        # Only needed while developing: slash/app command caching is instant.
        dev_guild = os.getenv("GUILD_ID", "").strip()
        self.dev_guild_id: int | None = int(dev_guild) if dev_guild.isdigit() else None

        self.db_path: str = os.getenv("DB_PATH", "data/bot.db").strip() or "data/bot.db"
        self.port: int = int(os.getenv("PORT", "8080") or 8080)

        self.color_main: int = _colour(os.getenv("COLOR_MAIN"), 0x5865F2)
        self.color_success: int = _colour(os.getenv("COLOR_SUCCESS"), 0x3BA55C)
        self.color_error: int = _colour(os.getenv("COLOR_ERROR"), 0xED4245)
        self.color_warning: int = _colour(os.getenv("COLOR_WARNING"), 0xFEE75C)

    @property
    def valid(self) -> bool:
        return bool(self.token)