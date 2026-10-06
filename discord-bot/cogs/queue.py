"""Support queue.

Staff add a member with `!qadd`, see everyone waiting with `!qlist`, call the
next person with `!qnext`, and remove them once they're finished with `!qdone`.
"""
from __future__ import annotations

import datetime as dt
import time

import discord
from discord.ext import commands

from utils import GREEN, RED, make_embed, send_log, set_footer, truncate

MAX_VISIBLE = 25


def queue_manager():
    """Allow admins, staff with manage_roles/manage_channels, or the configured
    queue role."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id in ctx.bot.config.owner_ids:
            return True
        perms = ctx.author.guild_permissions
        if perms.administrator or perms.manage_roles or perms.manage_channels:
            return True
        config = await ctx.bot.db.get_config(ctx.guild.id)
        role_id = config["queue_role"]
        if role_id:
            role = ctx.guild.get_role(role_id)
            if role is not None and role in ctx.author.roles:
                return True
        raise commands.MissingPermissions(["manage_roles"])

    return commands.check(predicate)


class ConfirmView(discord.ui.View):
    def __init__(self, bot: commands.Bot, author_id: int) -> None:
        super().__init__(timeout=60)
        self.bot = bot
        self.author_id = author_id

    @discord.ui.button(label="Yes, clear it", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        removed = await self.bot.db.queue_clear(interaction.guild.id)
        await interaction.edit_original_response(
            content=f"Queue cleared — **{removed}** entry/entries removed."
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled.")
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your confirmation.", ephemeral=True)
            return False
        return True


class Queue(commands.Cog, name="Queue"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------- internals
    async def _notify(self, guild: discord.Guild, embed: discord.Embed, ctx: commands.Context | None = None) -> None:
        """Post to the queue log channel, falling back to the log channel."""
        config = await self.bot.db.get_config(guild.id)
        if await send_log(guild, config["queue_channel"] or config["log_channel"], embed):
            return
        if ctx is not None:
            await ctx.send(embed=embed)

    @staticmethod
    def _ago(timestamp: float) -> str:
        seconds = max(int(time.time() - timestamp), 0)
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"

    # -------------------------------------------------------------- commands
    @commands.group(name="queue", aliases=["q"], invoke_without_command=True)
    @commands.guild_only()
    @queue_manager()
    async def queue(self, ctx: commands.Context) -> None:
        """Show the queue."""
        await ctx.invoke(self.qlist)

    @commands.command(name="qadd", description="Add someone to the queue.")
    @commands.guild_only()
    @queue_manager()
    async def qadd(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given") -> None:
        added = await self.bot.db.queue_add(ctx.guild.id, member.id, ctx.author.id, reason)
        if added is None:
            return await ctx.send(f"{member.mention} is already in the queue.")

        position = len(await self.bot.db.queue_list(ctx.guild.id))
        embed = make_embed(
            title="Added to queue",
            description=f"{member.mention} added at position **{position}**.",
            colour=GREEN,
        )
        embed.add_field(name="Reason", value=truncate(reason, 500), inline=False)
        embed.add_field(name="Added by", value=ctx.author.mention, inline=True)
        set_footer(embed, self.bot, f"Use `{ctx.bot.config.prefix}qlist` to see everyone waiting")
        await self._notify(ctx.guild, embed, ctx)
        await ctx.send(f"{member.mention} added to the queue at position **{position}**.", delete_after=8)

    @commands.command(name="qlist", aliases=["qshow"], description="Show everyone in the queue.")
    @commands.guild_only()
    @queue_manager()
    async def qlist(self, ctx: commands.Context) -> None:
        rows = await self.bot.db.queue_list(ctx.guild.id)
        if not rows:
            return await ctx.send("The queue is empty. :tada:")

        lines: list[str] = []
        for index, row in enumerate(rows[:MAX_VISIBLE], start=1):
            member = ctx.guild.get_member(row["user_id"])
            name = member.display_name if member else "left the server"
            who = member.mention if member else f"`{row['user_id']}`"
            reason = truncate(row["reason"] or "—", 60)
            lines.append(f"`{index}.` {who} — {name} · {self._ago(row['added_at'])} · {reason}")

        embed = make_embed(
            title=f"Queue — {len(rows)} waiting",
            description="\n".join(lines),
            colour=self.bot.config.color_main,
        )
        if len(rows) > MAX_VISIBLE:
            embed.set_footer(text=f"+{len(rows) - MAX_VISIBLE} more not shown")
        else:
            set_footer(embed, self.bot, f"Next up: <@{rows[0]['user_id']}>")
        await ctx.send(embed=embed)

    @commands.command(name="qnext", description="Call the next person in line.")
    @commands.guild_only()
    @queue_manager()
    async def qnext(self, ctx: commands.Context) -> None:
        rows = await self.bot.db.queue_list(ctx.guild.id)
        if not rows:
            return await ctx.send("Nobody is waiting in the queue.")

        row = rows[0]
        member = ctx.guild.get_member(row["user_id"])
        total = len(rows)
        reason = row["reason"] or "No reason given"
        who = member.mention if member is not None else f"`{row['user_id']}`"

        embed = make_embed(
            title="You're up!",
            description=(
                f"{who} you're next in line.\n\n"
                f"**Reason:** {truncate(reason, 500)}\n"
                f"**Waiting behind you:** {total - 1}"
            ),
            colour=self.bot.config.color_main,
        )
        set_footer(embed, self.bot, f"Called by {ctx.author.display_name}")
        await self._notify(ctx.guild, embed, ctx)

        if member is not None:
            try:
                await member.send(
                    embed=make_embed(
                        title="You're up in the queue",
                        description=f"Please head over to **{ctx.guild.name}** and wait for staff.\n\n**Reason:** {truncate(reason, 500)}",
                        colour=self.bot.config.color_main,
                    )
                )
            except discord.Forbidden:
                pass
        await ctx.send(f"Called <@{row['user_id']}> — use `{ctx.bot.config.prefix}qdone` when finished.", delete_after=8)

    @commands.command(name="qdone", aliases=["qremove", "qremoveuser"], description="Remove someone from the queue.")
    @commands.guild_only()
    @queue_manager()
    async def qdone(self, ctx: commands.Context, member: discord.Member) -> None:
        removed = await self.bot.db.queue_remove(ctx.guild.id, member.id)
        if removed is None:
            return await ctx.send(f"{member.mention} isn't in the queue.")

        waited = max(int(time.time() - removed["added_at"]), 0)
        remaining = len(await self.bot.db.queue_list(ctx.guild.id))
        embed = make_embed(
            title="Removed from queue",
            description=(
                f"{member.mention} was taken off the queue by {ctx.author.mention} after waiting "
                f"**{waited // 60}m {waited % 60}s**.\n**Still waiting:** {remaining}"
            ),
            colour=RED,
        )
        await self._notify(ctx.guild, embed, ctx)
        await ctx.send(f"{member.mention} removed from the queue.", delete_after=8)

    @commands.command(name="qmove", description="Move someone to a different spot in the queue.")
    @commands.guild_only()
    @queue_manager()
    async def qmove(self, ctx: commands.Context, member: discord.Member, position: int = 1) -> None:
        """Usage: `!qmove @member 1` — position 1 is the front of the line."""
        rows = await self.bot.db.queue_list(ctx.guild.id)
        ids = [int(r["user_id"]) for r in rows]
        if member.id not in ids:
            return await ctx.send(f"{member.mention} isn't in the queue.")

        ids.remove(member.id)
        index = max(0, min(position - 1, len(ids)))
        ids.insert(index, member.id)
        await self.bot.db.queue_reorder(ctx.guild.id, ids)
        await ctx.send(f"{member.mention} moved to position **{index + 1}**.")

    @commands.command(name="qping", description="Ping someone who is waiting.")
    @commands.guild_only()
    @queue_manager()
    async def qping(self, ctx: commands.Context, member: discord.Member) -> None:
        rows = await self.bot.db.queue_list(ctx.guild.id)
        ids = [int(r["user_id"]) for r in rows]
        if member.id not in ids:
            return await ctx.send(f"{member.mention} isn't in the queue.")
        position = ids.index(member.id) + 1
        await ctx.send(f"{member.mention} you are number **{position}** in the queue.")
        try:
            await member.send(
                f"You are number **{position}** in the queue for **{ctx.guild.name}**."
            )
        except discord.Forbidden:
            await ctx.send("(Couldn't DM them — DMs are probably closed.)", delete_after=8)

    @commands.command(name="qclear", description="Clear the whole queue.")
    @commands.guild_only()
    @queue_manager()
    @commands.has_permissions(manage_roles=True)
    async def qclear(self, ctx: commands.Context) -> None:
        await ctx.send("Clear the entire queue?", view=ConfirmView(self.bot, ctx.author.id))

    @commands.command(name="qsetup", description="Choose where queue updates are posted.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def qsetup(self, ctx: commands.Context, channel: discord.TextChannel = None, role: discord.Role = None) -> None:
        config = await self.bot.db.get_config(ctx.guild.id)
        if channel is None:
            current = f"<#{config['queue_channel']}>" if config["queue_channel"] else "the log channel"
            role_obj = ctx.guild.get_role(config["queue_role"]) if config["queue_role"] else None
            role_text = role_obj.mention if role_obj else "anyone with manage_roles"
            await ctx.send(f"Queue updates go to: {current}\nQueue staff role: {role_text}")
            return
        await self.bot.db.set_config(
            ctx.guild.id,
            queue_channel=channel.id,
            queue_role=role.id if role else None,
        )
        role_text = role.mention if role else "anyone with manage_roles"
        await ctx.send(f"Queue updates will be posted in {channel.mention}\nQueue staff: {role_text}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Queue(bot))