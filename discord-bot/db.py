"""Async SQLite persistence layer.

Everything the bot must remember across restarts lives here: per-server
settings, tickets, giveaways, the queue, moderation history, raid tracking and
the verification attempt counter.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

# Columns a guild is allowed to have in guild_config. Used as a whitelist so
# dynamic UPDATE statements can never be injected into.
CONFIG_COLUMNS: frozenset[str] = frozenset(
    {
        "log_channel",
        "welcome_channel",
        "welcome_message",
        "welcome_dm",
        "leave_message",
        "autorole_id",
        "verification_channel",
        "verification_role",
        "verification_mode",
        "ticket_category",
        "ticket_log_channel",
        "ticket_support_role",
        "ticket_counter",
        "ticket_panel_message",
        "queue_channel",
        "queue_role",
        "lockdown",
        "antispam_enabled",
        "antispam_limit",
        "antispam_window",
        "antispam_action",
        "antiraid_enabled",
        "antiraid_threshold",
        "antiraid_window",
        "linkblock_enabled",
        "linkblock_ignored",
        "mentionblock_enabled",
        "word_filter",
    }
)

DEFAULT_CONFIG: dict[str, Any] = {
    "log_channel": None,
    "welcome_channel": None,
    "welcome_message": None,
    "welcome_dm": None,
    "leave_message": None,
    "autorole_id": None,
    "verification_channel": None,
    "verification_role": None,
    "verification_mode": "captcha",
    "ticket_category": None,
    "ticket_log_channel": None,
    "ticket_support_role": None,
    "ticket_counter": 0,
    "ticket_panel_message": None,
    "queue_channel": None,
    "queue_role": None,
    "lockdown": 0,
    "antispam_enabled": 0,
    "antispam_limit": 6,
    "antispam_window": 8,
    "antispam_action": "delete",
    "antiraid_enabled": 0,
    "antiraid_threshold": 8,
    "antiraid_window": 15,
    "linkblock_enabled": 0,
    "linkblock_ignored": None,
    "mentionblock_enabled": 0,
    "word_filter": None,
}

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id          INTEGER PRIMARY KEY,
    log_channel       INTEGER,
    welcome_channel   INTEGER,
    welcome_message   TEXT,
    welcome_dm        TEXT,
    leave_message     TEXT,
    autorole_id       INTEGER,
    verification_channel INTEGER,
    verification_role INTEGER,
    verification_mode TEXT    DEFAULT 'captcha',
    ticket_category   INTEGER,
    ticket_log_channel INTEGER,
    ticket_support_role INTEGER,
    ticket_counter    INTEGER DEFAULT 0,
    ticket_panel_message INTEGER,
    queue_channel     INTEGER,
    queue_role        INTEGER,
    lockdown          INTEGER DEFAULT 0,
    antispam_enabled  INTEGER DEFAULT 0,
    antispam_limit    INTEGER DEFAULT 6,
    antispam_window   INTEGER DEFAULT 8,
    antispam_action   TEXT    DEFAULT 'delete',
    antiraid_enabled  INTEGER DEFAULT 0,
    antiraid_threshold INTEGER DEFAULT 8,
    antiraid_window   INTEGER DEFAULT 15,
    linkblock_enabled INTEGER DEFAULT 0,
    linkblock_ignored TEXT,
    mentionblock_enabled INTEGER DEFAULT 0,
    word_filter       TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    owner_id    INTEGER NOT NULL,
    number      INTEGER NOT NULL DEFAULT 0,
    subject     TEXT,
    status      TEXT    NOT NULL DEFAULT 'open',
    created_at  INTEGER NOT NULL,
    closed_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tickets_channel ON tickets(channel_id);
CREATE INDEX IF NOT EXISTS idx_tickets_guild   ON tickets(guild_id, status);

CREATE TABLE IF NOT EXISTS giveaways (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    prize       TEXT    NOT NULL,
    winners     INTEGER NOT NULL DEFAULT 1,
    ends_at     INTEGER NOT NULL,
    host_id     INTEGER NOT NULL,
    entrants    TEXT    NOT NULL DEFAULT '',
    ended       INTEGER NOT NULL DEFAULT 0,
    result      TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_giveaways_due ON giveaways(ended, ends_at);

CREATE TABLE IF NOT EXISTS queue (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    added_by  INTEGER NOT NULL,
    reason    TEXT,
    added_at  INTEGER NOT NULL,
    UNIQUE(guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS mod_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    action       TEXT    NOT NULL,
    reason       TEXT,
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_user ON mod_actions(guild_id, user_id, action);

CREATE TABLE IF NOT EXISTS verify_attempts (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS raid_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raid_recent ON raid_log(guild_id, created_at);

CREATE TABLE IF NOT EXISTS blacklist (
    user_id    INTEGER PRIMARY KEY,
    added_by   INTEGER NOT NULL,
    reason     TEXT,
    created_at INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.conn: aiosqlite.Connection | None = None
        self._config_cache: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------------ setup
    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    # --------------------------------------------------------------- plumbing
    @property
    def db(self) -> aiosqlite.Connection:
        if self.conn is None:
            raise RuntimeError("Database is not connected")
        return self.conn

    async def _fetchone(self, query: str, args: Iterable = ()) -> aiosqlite.Row | None:
        async with self.db.execute(query, tuple(args)) as cursor:
            return await cursor.fetchone()

    async def _fetchall(self, query: str, args: Iterable = ()) -> list[aiosqlite.Row]:
        async with self.db.execute(query, tuple(args)) as cursor:
            return await cursor.fetchall()

    async def _execute(self, query: str, args: Iterable = ()) -> int:
        cursor = await self.db.execute(query, tuple(args))
        await self.db.commit()
        return int(cursor.lastrowid or 0)

    # --------------------------------------------------------- guild settings
    async def get_config(self, guild_id: int) -> dict[str, Any]:
        """Config for a guild, defaults merged in. Cached in memory."""
        cached = self._config_cache.get(guild_id)
        if cached is not None:
            return cached

        row = await self._fetchone("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,))
        config = dict(DEFAULT_CONFIG)
        if row is not None:
            for key in row.keys():
                if key in DEFAULT_CONFIG:
                    config[key] = row[key]
        config["guild_id"] = guild_id
        self._config_cache[guild_id] = config
        return config

    async def set_config(self, guild_id: int, **fields: Any) -> None:
        updates = {k: v for k, v in fields.items() if k in CONFIG_COLUMNS}
        if not updates:
            return
        await self._execute("INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,))
        assignments = ", ".join(f"{column} = ?" for column in updates)
        await self._execute(
            f"UPDATE guild_config SET {assignments} WHERE guild_id = ?",
            (*updates.values(), guild_id),
        )
        self._config_cache.pop(guild_id, None)

    async def invalidate_config(self, guild_id: int | None = None) -> None:
        if guild_id is None:
            self._config_cache.clear()
        else:
            self._config_cache.pop(guild_id, None)

    # --------------------------------------------------------------- blacklist
    async def blacklist_add(self, user_id: int, added_by: int, reason: str | None) -> None:
        await self._execute(
            "INSERT OR REPLACE INTO blacklist (user_id, added_by, reason, created_at) VALUES (?, ?, ?, ?)",
            (user_id, added_by, reason, int(time.time())),
        )

    async def blacklist_remove(self, user_id: int) -> None:
        await self._execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))

    async def blacklist_contains(self, user_id: int) -> bool:
        return await self._fetchone("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,)) is not None

    async def blacklist_all(self) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM blacklist ORDER BY created_at DESC")

    # ------------------------------------------------------------ verification
    async def verify_bump(self, guild_id: int, user_id: int) -> int:
        await self._execute(
            """
            INSERT INTO verify_attempts (guild_id, user_id, attempts, updated_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET attempts = attempts + 1, updated_at = excluded.updated_at
            """,
            (guild_id, user_id, int(time.time())),
        )
        row = await self._fetchone(
            "SELECT attempts FROM verify_attempts WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return int(row["attempts"]) if row else 1

    async def verify_reset(self, guild_id: int, user_id: int) -> None:
        await self._execute(
            "DELETE FROM verify_attempts WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )

    # -------------------------------------------------------------- raid guard
    async def raid_join(self, guild_id: int, user_id: int) -> int:
        """Record a join and return how many joins happened in the last 60s."""
        now = int(time.time())
        await self._execute(
            "INSERT INTO raid_log (guild_id, user_id, created_at) VALUES (?, ?, ?)",
            (guild_id, user_id, now),
        )
        return await self.raid_recent(guild_id, 60)

    async def raid_recent(self, guild_id: int, seconds: int) -> int:
        cutoff = int(time.time()) - seconds
        row = await self._fetchone(
            "SELECT COUNT(*) AS total FROM raid_log WHERE guild_id = ? AND created_at >= ?",
            (guild_id, cutoff),
        )
        return int(row["total"]) if row else 0

    async def raid_recent_users(self, guild_id: int, seconds: int) -> list[int]:
        cutoff = int(time.time()) - seconds
        rows = await self._fetchall(
            "SELECT user_id FROM raid_log WHERE guild_id = ? AND created_at >= ? ORDER BY created_at",
            (guild_id, cutoff),
        )
        return [int(row["user_id"]) for row in rows]

    async def raid_prune(self, guild_id: int, older_than_seconds: int = 3600) -> None:
        cutoff = int(time.time()) - older_than_seconds
        await self._execute("DELETE FROM raid_log WHERE guild_id = ? AND created_at < ?", (guild_id, cutoff))

    # ------------------------------------------------------------ mod history
    async def action_add(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: str | None = None,
    ) -> int:
        return await self._execute(
            "INSERT INTO mod_actions (guild_id, user_id, moderator_id, action, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, action, reason, int(time.time())),
        )

    async def actions_for(
        self,
        guild_id: int,
        user_id: int,
        action: str | None = None,
        limit: int = 10,
    ) -> list[aiosqlite.Row]:
        if action:
            return await self._fetchall(
                "SELECT * FROM mod_actions WHERE guild_id = ? AND user_id = ? AND action = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (guild_id, user_id, action, limit),
            )
        return await self._fetchall(
            "SELECT * FROM mod_actions WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT ?",
            (guild_id, user_id, limit),
        )

    async def clear_actions(self, guild_id: int, user_id: int, action: str | None = None) -> int:
        if action:
            cursor = await self.db.execute(
                "DELETE FROM mod_actions WHERE guild_id = ? AND user_id = ? AND action = ?",
                (guild_id, user_id, action),
            )
        else:
            cursor = await self.db.execute(
                "DELETE FROM mod_actions WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
        await self.db.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------ queue
    async def queue_add(self, guild_id: int, user_id: int, added_by: int, reason: str | None) -> int | None:
        """Returns the new row id, or None when the user is already queued."""
        existing = await self._fetchone(
            "SELECT 1 FROM queue WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        if existing is not None:
            return None
        return await self._execute(
            "INSERT INTO queue (guild_id, user_id, added_by, reason, added_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, added_by, reason, int(time.time())),
        )

    async def queue_remove(self, guild_id: int, user_id: int) -> aiosqlite.Row | None:
        row = await self._fetchone(
            "SELECT * FROM queue WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        if row is None:
            return None
        await self._execute(
            "DELETE FROM queue WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return row

    async def queue_list(self, guild_id: int) -> list[aiosqlite.Row]:
        return await self._fetchall(
            "SELECT * FROM queue WHERE guild_id = ? ORDER BY added_at ASC, id ASC",
            (guild_id,),
        )

    async def queue_clear(self, guild_id: int) -> int:
        cursor = await self.db.execute("DELETE FROM queue WHERE guild_id = ?", (guild_id,))
        await self.db.commit()
        return cursor.rowcount

    async def queue_reorder(self, guild_id: int, user_ids: list[int]) -> None:
        """Rewrite the queue order to match `user_ids` (index 0 = first in line)."""
        base = int(time.time()) - len(user_ids)
        for index, user_id in enumerate(user_ids):
            await self.db.execute(
                "UPDATE queue SET added_at = ? WHERE guild_id = ? AND user_id = ?",
                (base + index, guild_id, user_id),
            )
        await self.db.commit()

    # ---------------------------------------------------------------- tickets
    async def ticket_create(
        self,
        guild_id: int,
        channel_id: int,
        owner_id: int,
        number: int,
        subject: str | None,
    ) -> int:
        return await self._execute(
            "INSERT INTO tickets (guild_id, channel_id, owner_id, number, subject, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (guild_id, channel_id, owner_id, number, subject, int(time.time())),
        )

    async def ticket_by_channel(self, channel_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))

    async def ticket_close(self, channel_id: int) -> None:
        await self._execute(
            "UPDATE tickets SET status = 'closed', closed_at = ? WHERE channel_id = ?",
            (int(time.time()), channel_id),
        )

    async def ticket_reopen(self, channel_id: int) -> None:
        await self._execute(
            "UPDATE tickets SET status = 'open', closed_at = NULL WHERE channel_id = ?",
            (channel_id,),
        )

    async def tickets_open(self, guild_id: int) -> list[aiosqlite.Row]:
        return await self._fetchall(
            "SELECT * FROM tickets WHERE guild_id = ? AND status = 'open' ORDER BY created_at",
            (guild_id,),
        )

    async def next_ticket_number(self, guild_id: int) -> int:
        row = await self._fetchone(
            "SELECT ticket_counter FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        )
        counter = int(row["ticket_counter"]) if row and row["ticket_counter"] else 0
        counter += 1
        await self.set_config(guild_id, ticket_counter=counter)
        return counter

    # -------------------------------------------------------------- giveaways
    async def giveaway_create(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        prize: str,
        winners: int,
        ends_at: int,
        host_id: int,
    ) -> int:
        return await self._execute(
            "INSERT INTO giveaways (guild_id, channel_id, message_id, prize, winners, ends_at, host_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, message_id, prize, winners, ends_at, host_id, int(time.time())),
        )

    async def giveaway_get(self, giveaway_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))

    async def giveaway_by_message(self, guild_id: int, message_id: int) -> aiosqlite.Row | None:
        return await self._fetchone(
            "SELECT * FROM giveaways WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id),
        )

    async def giveaways_due(self, now: int | None = None) -> list[aiosqlite.Row]:
        now = now if now is not None else int(time.time())
        return await self._fetchall(
            "SELECT * FROM giveaways WHERE ended = 0 AND ends_at <= ?", (now,)
        )

    async def giveaways_list(self, guild_id: int, limit: int = 25) -> list[aiosqlite.Row]:
        return await self._fetchall(
            "SELECT * FROM giveaways WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?",
            (guild_id, limit),
        )

    async def giveaway_add_entrant(self, giveaway_id: int, user_id: int) -> bool:
        row = await self._fetchone("SELECT entrants FROM giveaways WHERE id = ?", (giveaway_id,))
        if row is None:
            return False
        entrants = [x for x in (row["entrants"] or "").split(",") if x]
        if str(user_id) in entrants:
            return False
        entrants.append(str(user_id))
        await self._execute(
            "UPDATE giveaways SET entrants = ? WHERE id = ?",
            (",".join(entrants), giveaway_id),
        )
        return True

    async def giveaway_remove_entrant(self, giveaway_id: int, user_id: int) -> bool:
        row = await self._fetchone("SELECT entrants FROM giveaways WHERE id = ?", (giveaway_id,))
        if row is None:
            return False
        entrants = [x for x in (row["entrants"] or "").split(",") if x and x != str(user_id)]
        await self._execute(
            "UPDATE giveaways SET entrants = ? WHERE id = ?",
            (",".join(entrants), giveaway_id),
        )
        return True

    async def giveaway_finish(self, giveaway_id: int, result: dict[str, Any]) -> None:
        await self._execute(
            "UPDATE giveaways SET ended = 1, result = ? WHERE id = ?",
            (json.dumps(result), giveaway_id),
        )

    async def giveaway_reopen(self, giveaway_id: int) -> None:
        """Mark a giveaway as running again (used by reroll)."""
        await self._execute(
            "UPDATE giveaways SET ended = 0, result = NULL WHERE id = ?",
            (giveaway_id,),
        )

    async def giveaway_set_message(self, giveaway_id: int, message_id: int) -> None:
        await self._execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?", (message_id, giveaway_id)
        )

    async def giveaway_delete(self, giveaway_id: int) -> None:
        await self._execute("DELETE FROM giveaways WHERE id = ?", (giveaway_id,))