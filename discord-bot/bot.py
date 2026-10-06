"""Entry point. Wires up the database, cogs, error handling and the HTTP
health endpoint that free hosts use to decide the process is alive."""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Optional

import discord
from aiohttp import web
from discord.ext import commands

from config import Config
from db import Database

EXTENSIONS = [
    "cogs.info",
    "cogs.moderation",
    "cogs.welcome",
    "cogs.verification",
    "cogs.tickets",
    "cogs.giveaways",
    "cogs.queue",
    "cogs.logs",
    "cogs.security",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")


class BotHelp(commands.HelpCommand):
    """Embed-based help so !help does not spam the channel."""

    def __init__(self) -> None:
        super().__init__(
            command_attrs={"help": "Show this help menu", "aliases": ["commands", "h"]},
            context_settings={"help_command": self},
        )

    async def send_help_command(self, ctx: commands.Context) -> None:
        embed = discord.Embed(
            title=f"{self.context.bot.user.name} — commands",
            colour=ctx.bot.config.color_main,
        )
        groups: dict[str, list[str]] = {}
        for command in sorted(ctx.bot.commands, key=lambda c: c.qualified_name):
            if command.hidden or not command.name:
                continue
            root = command.qualified_name.split()[0]
            groups.setdefault(root, []).append(
                f"`{ctx.clean_prefix}{command.qualified_name} {command.signature or ''}`".strip()
            )
        for root, lines in groups.items():
            embed.add_field(name=f"• {root}", value=truncate_lines("\n".join(lines), 1024), inline=False)
        embed.set_footer(text=f"Prefix: {ctx.prefix} • {len(ctx.bot.commands)} commands")
        await ctx.send(embed=embed)


def truncate_lines(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Bot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True   # needed for command prefixes + security checks
        intents.members = True          # needed for join/leave events and moderation
        intents.presences = False

        super().__init__(
            command_prefix=config.prefix,
            intents=intents,
            help_command=BotHelp(),
            description="A modular, always-on community bot.",
            max_messages=None,            # full message cache so delete-logs work
            case_insensitive=True,
            strip_after_prefix=True,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.config = config
        self.db: Optional[Database] = None
        self.start_time: float = time.time()
        self._web_runner: Optional[web.AppRunner] = None

    # ------------------------------------------------------------- lifecycle
    async def setup_hook(self) -> None:
        self.start_time = time.time()
        self.db = Database(self.config.db_path)
        await self.db.connect()
        log.info("Database ready at %s", self.config.db_path)

        for extension in EXTENSIONS:
            try:
                await self.load_extension(extension)
                log.info("Loaded extension %s", extension)
            except Exception:
                log.exception("Failed to load extension %s", extension)

        if self.user is not None and self.config.dev_guild_id:
            try:
                await self.tree.sync(guild=discord.Object(id=self.config.dev_guild_id))
            except Exception:
                log.exception("App command sync failed")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID %s)", self.user, self.user.id)
        log.info("Connected to %d guild(s)", len(self.guilds))
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)} server(s) | {self.config.prefix}help",
            )
        )

    async def on_resumed(self) -> None:
        log.info("Session resumed — gateway reconnected")

    async def on_disconnect(self) -> None:
        log.warning("Disconnected from Discord gateway")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        await self.process_commands(message)

    async def close(self) -> None:
        if self._web_runner is not None:
            await self._web_runner.cleanup()
        if self.db is not None:
            await self.db.close()
        log.info("Shutdown complete")
        await super().close()

    # -------------------------------------------------------- error handling
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        error = getattr(error, "original", error)

        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            missing = ", ".join(sorted(error.missing_permissions).replace("_", " "))
            return await _error(ctx, f"You need the following permission(s): `{missing}`")
        if isinstance(error, commands.BotMissingPermissions):
            missing = ", ".join(sorted(error.missing_permissions).replace("_", " "))
            return await _error(ctx, f"I'm missing the permission(s): `{missing}`")
        if isinstance(error, commands.MissingRole):
            return await _error(ctx, f"You need the role {error.missing_role.mention} to do that.")
        if isinstance(error, commands.CommandOnCooldown):
            wait = error.retry_after
            return await _error(ctx, f"Slow down — try again in **{wait:.0f}s**.")
        if isinstance(error, commands.UserInputError):
            return await _error(ctx, f"{error}")
        if isinstance(error, discord.Forbidden):
            return await _error(ctx, "I'm not allowed to do that (missing permissions).")
        if isinstance(error, discord.HTTPException):
            return await _error(ctx, "Discord rejected that request. Try again in a moment.")

        log.error("Unhandled command error: %s", error, exc_info=error)
        await _error(ctx, "Something broke on my end. The error has been logged.")

    async def on_error(self, event: str, *args, **kwargs) -> None:
        log.error("Unhandled error in event %s", event, exc_info=kwargs.get("exc_info", True))


async def _error(ctx: commands.Context, message: str) -> None:
    embed = discord.Embed(description=message, colour=ctx.bot.config.color_error)
    try:
        await ctx.send(embed=embed)
    except discord.HTTPException:
        pass


async def start_health_server(bot: Bot, port: int) -> None:
    """Tiny HTTP server so free hosts (Northflank, Render) can health-check us.

    Discord bots generate no inbound HTTP traffic, so without this endpoint
    hosts think the service is idle.
    """
    app = web.Application()
    app.router.add_get("/health", lambda _request: web.Response(text="ok"))
    app.router.add_get("/", lambda _request: web.Response(text="bot is running"))

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    bot._web_runner = runner
    log.info("Health endpoint listening on 0.0.0.0:%d", port)


def main() -> None:
    config = Config()
    if not config.valid:
        print("DISCORD_TOKEN is missing. Copy .env.example to .env and paste your bot token in.")
        sys.exit(1)

    bot = Bot(config)
    log.info("Starting with prefix %r", config.prefix)

    async def run() -> None:
        async with bot:
            await start_health_server(bot, config.port)
            await bot.start(config.token)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down")
    except discord.LoginFailure:
        log.error("Invalid token. Check DISCORD_TOKEN in your .env file.")
    except Exception:
        log.exception("Fatal error while running the bot")
        sys.exit(1)


if __name__ == "__main__":
    main()