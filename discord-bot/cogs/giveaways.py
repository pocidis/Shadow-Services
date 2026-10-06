"""Giveaways. Entries live in SQLite, so giveaways survive a restart."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random

import discord
from discord.ext import commands, tasks

from utils import (
    GREEN,
    YELLOW,
    fmt_duration,
    make_embed,
    parse_duration,
    truncate,
)

log = logging.getLogger("cogs.giveaways")

ENTRY_EMOJI = "\U0001f389"  # party popper
CHECK_INTERVAL = 15  # seconds between checks for finished giveaways

USAGE = (
    "Usage:\n"
    "`!giveaway #channel 10m 1 Nitro 3 months`\n"
    "`!giveaway 10m 1 Nitro` — in the current channel"
)


class Giveaways(commands.Cog, name="Giveaways"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.check_due.start()

    async def cog_unload(self) -> None:
        self.check_due.cancel()
        try:
            await self.check_due
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------- internals
    def _embed(
        self,
        *,
        giveaway_id: int | None = None,
        prize: str,
        host_id: int,
        winners: int,
        ends_at: int,
        entrants: int,
        ended: bool = False,
        winner_text: str | None = None,
        footer: str | None = None,
    ) -> discord.Embed:
        embed = make_embed(colour=GREEN if ended else YELLOW)
        embed.title = "\U0001f389 Giveaway"
        embed.add_field(name="Prize", value=truncate(prize, 1000) or "Nothing", inline=False)
        embed.add_field(name="Hosted by", value=f"<@{host_id}>", inline=True)
        embed.add_field(name="Winners", value=str(winners), inline=True)
        embed.add_field(name="Entrants", value=str(entrants), inline=True)

        when = discord.utils.format_dt(
            dt.datetime.fromtimestamp(ends_at, dt.timezone.utc), "R"
        )
        if ended:
            embed.add_field(name="Ended", value=when, inline=True)
        else:
            left = int(ends_at - dt.datetime.now(dt.timezone.utc).timestamp())
            embed.add_field(name="Ends", value=when, inline=True)
            embed.add_field(name="Time left", value=fmt_duration(left), inline=True)

        if winner_text:
            embed.add_field(name="Winner(s)", value=winner_text, inline=False)

        parts = [f"Giveaway ID: {giveaway_id}" if giveaway_id else "Creating…"]
        if footer:
            parts.append(footer)
        embed.set_footer(text=" • ".join(parts))
        return embed

    async def _pick_winners(self, row, count: int) -> list[discord.Member]:
        entrants = [int(x) for x in (row["entrants"] or "").split(",") if x]
        if not entrants:
            return []
        guild = self.bot.get_guild(row["guild_id"])
        if guild is None:
            return []

        winners: list[discord.Member] = []
        for user_id in random.sample(entrants, min(count, len(entrants))):
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    continue
            if member is not None:
                winners.append(member)
        return winners

    async def finish(self, row, reroll: bool = False) -> None:
        """Announce winners and mark the giveaway as finished."""
        entrants = [x for x in (row["entrants"] or "").split(",") if x]
        winners = await self._pick_winners(row, int(row["winners"]))
        winner_text = ", ".join(w.mention for w in winners) if winners else "No valid entries."

        channel = self.bot.get_channel(row["channel_id"])
        if channel is not None:
            embed = self._embed(
                giveaway_id=row["id"],
                prize=row["prize"],
                host_id=row["host_id"],
                winners=int(row["winners"]),
                ends_at=int(row["ends_at"]),
                entrants=len(entrants),
                ended=True,
                winner_text=winner_text,
            )
            embed.title = "\U0001f973 Reroll!" if reroll else "\U0001f389 Giveaway ended!"
            try:
                message = await channel.send(embed=embed)
                if winners:
                    await message.reply(f"Congratulations {winner_text}!")
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Could not announce giveaway %s", row["id"])

        await self.bot.db.giveaway_finish(
            row["id"],
            {
                "winners": [w.id for w in winners],
                "reroll": reroll,
                "at": int(dt.datetime.now(dt.timezone.utc).timestamp()),
            },
        )

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def check_due(self) -> None:
        """Background task: end giveaways whose time is up."""
        if not self.bot.is_ready():
            return
        try:
            due = await self.bot.db.giveaways_due()
        except Exception:
            log.exception("Could not read due giveaways")
            return
        for row in due:
            try:
                await self.finish(row)
            except Exception:
                log.exception("Failed to finish giveaway %s", row["id"])

    @check_due.error
    async def check_due_error(self, error: Exception) -> None:
        log.error("Giveaway ticker error: %s", error)

    # -------------------------------------------------------------- commands
    @commands.command(name="giveaway", aliases=["gstart", "gcreate"])
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def giveaway(self, ctx: commands.Context) -> None:
        """Start a giveaway: `!giveaway [#channel] <duration> <winners> <prize>`"""
        pieces = ctx.message.content.split(None, 1)
        content = pieces[1] if len(pieces) > 1 else ""
        for mention in ctx.message.channel_mentions:
            content = content.replace(mention.mention, " ")
        parts = content.split()

        if len(parts) < 3:
            return await ctx.send(USAGE)

        duration, winners_text = parts[0], parts[1]
        prize = " ".join(parts[2:]).strip()
        channel = ctx.message.channel_mentions[0] if ctx.message.channel_mentions else ctx.channel

        try:
            seconds = parse_duration(duration)
        except ValueError as exc:
            return await ctx.send(f"{exc}\n{USAGE}")
        try:
            winner_count = int(winners_text)
        except ValueError:
            return await ctx.send(f"`{winners_text}` is not a valid number of winners.\n{USAGE}")

        if seconds < 10:
            return await ctx.send("A giveaway must run for at least 10 seconds.")
        if not 1 <= winner_count <= 20:
            return await ctx.send("Winners must be between 1 and 20.")
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("That channel doesn't accept messages.")

        ends_at = int((dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).timestamp())
        host_id = ctx.author.id

        giveaway_id = await self.bot.db.giveaway_create(
            ctx.guild.id, channel.id, 0, prize, winner_count, ends_at, host_id
        )
        try:
            message = await channel.send(
                embed=self._embed(
                    giveaway_id=giveaway_id,
                    prize=prize,
                    host_id=host_id,
                    winners=winner_count,
                    ends_at=ends_at,
                    entrants=0,
                    footer=f"React with {ENTRY_EMOJI} to enter",
                )
            )
            await self.bot.db.giveaway_set_message(giveaway_id, message.id)
            await message.add_reaction(ENTRY_EMOJI)
        except discord.HTTPException as exc:
            await self.bot.db.giveaway_delete(giveaway_id)
            return await ctx.send(f"Couldn't post the giveaway: {exc}")

        await ctx.send(f"Giveaway live in {channel.mention} — ID `{giveaway_id}`.")

    @commands.command(name="gwend", aliases=["gend"], description="End a giveaway now.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def gwend(self, ctx: commands.Context, giveaway_id: int) -> None:
        row = await self.bot.db.giveaway_get(giveaway_id)
        if row is None or row["guild_id"] != ctx.guild.id:
            return await ctx.send("No giveaway with that ID in this server.")
        if row["ended"]:
            return await ctx.send("That giveaway already ended.")
        await self.finish(row)
        await ctx.send("Giveaway ended.")

    @commands.command(name="gwreroll", description="Draw new winners for an ended giveaway.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def gwreroll(self, ctx: commands.Context, giveaway_id: int) -> None:
        row = await self.bot.db.giveaway_get(giveaway_id)
        if row is None or row["guild_id"] != ctx.guild.id:
            return await ctx.send("No giveaway with that ID in this server.")
        if not [x for x in (row["entrants"] or "").split(",") if x]:
            return await ctx.send("Nobody entered that giveaway.")
        await self.bot.db.giveaway_reopen(giveaway_id)
        await self.finish(await self.bot.db.giveaway_get(giveaway_id), reroll=True)

    @commands.command(name="gwlist", aliases=["glist"], description="List recent giveaways.")
    @commands.guild_only()
    async def gwlist(self, ctx: commands.Context) -> None:
        rows = await self.bot.db.giveaways_list(ctx.guild.id, limit=15)
        if not rows:
            return await ctx.send("No giveaways here yet.")
        embed = make_embed(title="Recent giveaways", colour=self.bot.config.color_main)
        for row in rows:
            state = "ended" if row["ended"] else "running"
            when = discord.utils.format_dt(
                dt.datetime.fromtimestamp(row["ends_at"], dt.timezone.utc), "R"
            )
            embed.add_field(
                name=f"`#{row['id']}` — {truncate(row['prize'], 60)}",
                value=f"**{state}** • winners: {row['winners']} • ends: {when}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="gwjoin", description="Enter a giveaway without clicking the reaction.")
    @commands.guild_only()
    async def gwjoin(self, ctx: commands.Context, giveaway_id: int) -> None:
        row = await self.bot.db.giveaway_get(giveaway_id)
        if row is None or row["guild_id"] != ctx.guild.id:
            return await ctx.send("No giveaway with that ID in this server.")
        if row["ended"]:
            return await ctx.send("That giveaway already ended.")
        if await self.bot.db.giveaway_add_entrant(giveaway_id, ctx.author.id):
            await ctx.send(f"You're entered. Good luck, {ctx.author.mention}!")
        else:
            await ctx.send("You're already entered.")

    @commands.command(name="gwleave", description="Leave a giveaway you entered.")
    @commands.guild_only()
    async def gwleave(self, ctx: commands.Context, giveaway_id: int) -> None:
        await self.bot.db.giveaway_remove_entrant(giveaway_id, ctx.author.id)
        await ctx.send("You've left that giveaway.")

    # -------------------------------------------------------------- reactions
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.emoji.name != ENTRY_EMOJI:
            return
        row = await self.bot.db.giveaway_by_message(payload.guild_id, payload.message_id)
        if row is not None and not row["ended"]:
            await self.bot.db.giveaway_add_entrant(row["id"], payload.user_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.emoji.name != ENTRY_EMOJI:
            return
        row = await self.bot.db.giveaway_by_message(payload.guild_id, payload.message_id)
        if row is not None and not row["ended"]:
            await self.bot.db.giveaway_remove_entrant(row["id"], payload.user_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Giveaways(bot))