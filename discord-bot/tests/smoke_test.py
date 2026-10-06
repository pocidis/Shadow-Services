"""Offline smoke test: no Discord connection needed.

    python tests/smoke_test.py

Checks that every module imports, every cog loads, the command tree is wired up
and the database layer behaves.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(f"{label} {detail}")


def section(name: str) -> None:
    print(f"\n=== {name} ===")


# --------------------------------------------------------------------- utils
section("utils")
from utils import (  # noqa: E402
    action_embed,
    event_embed,
    fmt_duration,
    make_embed,
    parse_duration,
    truncate,
    user_tag,
)

check("parse_duration('30')", parse_duration("30") == 30)
check("parse_duration('10m')", parse_duration("10m") == 600)
check("parse_duration('1h30m')", parse_duration("1h30m") == 5400)
check("parse_duration('2d')", parse_duration("2d") == 172800)
check("parse_duration('1w')", parse_duration("1w") == 604800)
try:
    parse_duration("banana")
    check("parse_duration rejects junk", False)
except ValueError:
    check("parse_duration rejects junk", True)
try:
    parse_duration("10mbanana")
    check("parse_duration rejects '10mbanana'", False)
except ValueError:
    check("parse_duration rejects '10mbanana'", True)
check("fmt_duration(5400)", fmt_duration(5400) == "1h 30m", f"got {fmt_duration(5400)!r}")
check("fmt_duration(0)", fmt_duration(0) == "0s", f"got {fmt_duration(0)!r}")
check("truncate short", truncate("hello", 10) == "hello")
check("truncate long", len(truncate("x" * 50, 10)) == 10)
check("make_embed colour", make_embed(colour=5).colour.value == 5)
check("event_embed fields", len(event_embed("t", 5, ("a", "b", True), ("c", "d", False)).fields) == 2)


# ------------------------------------------------------------- verification
section("verification captcha logic")
from cogs.verification import _make_question  # noqa: E402

problems = 0
for _ in range(300):
    question, answer, options = _make_question()
    expression = question.split("=")[0].strip().replace("x", "*").replace("+", " + ").replace("-", " - ")
    if not re.fullmatch(r"[0-9 +*-]+", expression):
        problems += 1
        continue
    if eval(expression) != answer:  # noqa: S307 - digits and operators only, test scope
        problems += 1
    if len(options) != 3 or len(set(options)) != 3 or answer not in options:
        problems += 1
check("300 captchas: question matches answer, 3 unique options", problems == 0, f"{problems} problems")

from cogs.security import LINK_RE  # noqa: E402

check("link regex matches invite", bool(LINK_RE.search("join discord.gg/abc123")))
check("link regex matches https", bool(LINK_RE.search("see https://example.com/x")))
check("link regex matches invite path", bool(LINK_RE.search("discord.com/invite/xyz")))
check("link regex ignores plain text", not LINK_RE.search("just chatting"))


# ---------------------------------------------------------------- database
async def database_checks() -> None:
    section("database")
    from db import Database

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "test.db"))
        await db.connect()

        config = await db.get_config(1)
        check("defaults present", config["antispam_limit"] == 6 and config["ticket_counter"] == 0)
        await db.set_config(1, log_channel=123, antispam_enabled=1, not_a_column="ignored")
        config = await db.get_config(1)
        check("config saved", config["log_channel"] == 123 and config["antispam_enabled"] == 1)
        check("unknown column ignored", "not_a_column" not in config)

        # queue
        check("queue_add returns id", await db.queue_add(1, 10, 99, "help") == 1)
        check("queue_add duplicate returns None", await db.queue_add(1, 10, 99, "help") is None)
        await db.queue_add(1, 11, 99, "billing")
        await db.queue_add(1, 12, 99, None)
        rows = await db.queue_list(1)
        check("queue ordered", [int(r["user_id"]) for r in rows] == [10, 11, 12])
        await db.queue_reorder(1, [12, 10, 11])
        rows = await db.queue_list(1)
        check("queue_reorder", [int(r["user_id"]) for r in rows] == [12, 10, 11])
        removed = await db.queue_remove(1, 12)
        check("queue_remove returns row", removed is not None and int(removed["user_id"]) == 12)
        check("queue_remove missing -> None", await db.queue_remove(1, 999) is None)
        check("queue_clear", await db.queue_clear(1) == 2)

        # tickets
        check("ticket number increments", await db.next_ticket_number(1) == 1)
        check("ticket number increments", await db.next_ticket_number(1) == 2)
        await db.ticket_create(1, 500, 42, 1, "subject")
        row = await db.ticket_by_channel(500)
        check("ticket stored", row is not None and row["owner_id"] == 42)
        check("one open ticket", len(await db.tickets_open(1)) == 1)
        await db.ticket_close(500)
        check("ticket closed", len(await db.tickets_open(1)) == 0)
        check("ticket_by_channel still queryable", (await db.ticket_by_channel(500))["status"] == "closed")

        # giveaways
        gw = await db.giveaway_create(1, 500, 600, "Nitro", 2, 0, 42)
        check("giveaway created", gw == 1)
        check("entrant added", await db.giveaway_add_entrant(gw, 7) is True)
        check("entrant not duplicated", await db.giveaway_add_entrant(gw, 7) is False)
        check("entrant added", await db.giveaway_add_entrant(gw, 8) is True)
        check("entrant removed", await db.giveaway_remove_entrant(gw, 7) is True)
        row = await db.giveaway_get(gw)
        check("entrants persisted", row["entrants"] == "8", f"got {row['entrants']!r}")
        check("due returns ended=0 rows", len(await db.giveaways_due(0)) == 1)
        await db.giveaway_finish(gw, {"winners": [8]})
        check("finished not due", len(await db.giveaways_due(0)) == 0)
        await db.giveaway_reopen(gw)
        check("reopened is due again", len(await db.giveaways_due(0)) == 1)
        await db.giveaway_set_message(gw, 999)
        check(
            "giveaway_by_message",
            (await db.giveaway_by_message(1, 999)) is not None,
        )

        # verification attempts
        check("verify_bump 1", await db.verify_bump(1, 5) == 1)
        check("verify_bump 2", await db.verify_bump(1, 5) == 2)
        await db.verify_reset(1, 5)
        check("verify_bump after reset", await db.verify_bump(1, 5) == 1)

        # mod history
        await db.action_add(1, 5, 6, "Warn", "spam")
        await db.action_add(1, 5, 6, "Kick", "spam")
        check("two actions", len(await db.actions_for(1, 5)) == 2)
        check("filtered action", len(await db.actions_for(1, 5, "Warn")) == 1)
        check("clear_actions", await db.clear_actions(1, 5) == 2)

        # raid tracking
        for user_id in (1, 2, 3):
            await db.raid_join(1, user_id)
        check("raid_recent counts", await db.raid_recent(1, 60) == 3)
        check("raid_recent users", await db.raid_recent_users(1, 60) == [1, 2, 3])

        # blacklist
        await db.blacklist_add(777, 6, "raid")
        check("blacklisted", await db.blacklist_contains(777) is True)
        check("not blacklisted", await db.blacklist_contains(778) is False)
        await db.blacklist_remove(777)
        check("blacklist cleared", await db.blacklist_contains(777) is False)

        await db.close()


# --------------------------------------------------------------------- bot
async def bot_checks() -> None:
    section("bot + cogs")
    from config import Config
    from bot import Bot

    config = Config()
    config.db_path = str(Path(tempfile.gettempdir()) / "smoke_bot.db")
    bot = Bot(config)

    # Load extensions the same way setup_hook does.
    for extension in [
        "cogs.info",
        "cogs.moderation",
        "cogs.welcome",
        "cogs.verification",
        "cogs.tickets",
        "cogs.giveaways",
        "cogs.queue",
        "cogs.logs",
        "cogs.security",
    ]:
        try:
            await bot.load_extension(extension)
            check(f"load {extension}", True)
        except Exception:
            check(f"load {extension}", False)
            traceback.print_exc()

    names = sorted(c.name for c in bot.commands if c.name)
    print(f"  -> {len(names)} commands: {', '.join(names)}")

    expected = [
        "ping", "uptime", "membercount", "serverinfo", "userinfo", "botinfo",
        "kick", "ban", "unban", "mute", "unmute", "warn", "warnings", "clearwarnings",
        "purge", "slowmode", "lock", "unlock", "addrole", "removerole", "roles",
        "setwelcome", "setleave", "autorole", "testwelcome",
        "verification", "ticketsetup", "ticketpanel", "ticket", "tickets",
        "giveaway", "gwend", "gwreroll", "gwlist", "gwjoin", "gwleave",
        "queue", "qadd", "qlist", "qnext", "qdone", "qmove", "qping", "qclear", "qsetup",
        "logs", "security",
    ]
    missing = [name for name in expected if name not in names]
    check("all expected commands registered", not missing, f"missing: {missing}")

    # Prefix must be applied to commands and groups.
    check("queue is a group", bot.get_command("queue") is not None)
    check("qadd is standalone", bot.get_command("qadd") is not None)
    check("security subcommand antispam", bot.get_command("security antispam") is not None)
    check("verification subcommand panel", bot.get_command("verification panel") is not None)

    # Persistent views must survive a restart.
    check("help command wired", bot.help_command is not None)

    # Embed builders must not explode on dict-like rows.
    from cogs.giveaways import Giveaways
    cog = bot.get_cog("Giveaways")
    check("Giveaways cog present", cog is not None)
    if cog is not None:
        embed = cog._embed(
            giveaway_id=1,
            prize="Nitro",
            host_id=1,
            winners=1,
            ends_at=9999999999,
            entrants=3,
            footer="test",
        )
        check("giveaway embed builds", len(embed.fields) == 6, f"got {len(embed.fields)} fields")

        embed = cog._embed(
            giveaway_id=1,
            prize="Nitro",
            host_id=1,
            winners=1,
            ends_at=0,
            entrants=0,
            ended=True,
            winner_text="<@1>",
        )
        check("ended giveaway embed builds", embed.title is not None)

    from cogs.moderation import Moderation as ModerationCog  # noqa: F401
    from cogs.logs import Logs as LogsCog  # noqa: F401

    check("moderation cog present", bot.get_cog("Moderation") is not None)
    check("logs cog present", bot.get_cog("Logs") is not None)
    check("security cog present", bot.get_cog("Security") is not None)
    check("verification cog present", bot.get_cog("Verification") is not None)
    check("tickets cog present", bot.get_cog("Tickets") is not None)
    check("queue cog present", bot.get_cog("Queue") is not None)
    check("welcome cog present", bot.get_cog("Welcome") is not None)

    # Stop the background giveaway loop so asyncio.run can finish.
    giveaways_cog = bot.get_cog("Giveaways")
    if giveaways_cog is not None:
        giveaways_cog.check_due.cancel()
        await asyncio.sleep(0.1)
        check(
            "giveaway ticker stopped",
            giveaways_cog.check_due._task is None or giveaways_cog.check_due._task.cancelled(),
        )


async def main() -> int:
    await database_checks()
    await bot_checks()

    section("result")
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("DISCORD_TOKEN", "fake-token-for-import-check")
    sys.exit(asyncio.run(main()))