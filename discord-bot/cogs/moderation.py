"""Moderation commands: kick, ban, mute (timeout), warns, purge, slowmode, lock."""
from __future__ import annotations

import datetime as dt

import discord
from discord.ext import commands

from utils import (
    RED,
    YELLOW,
    action_embed,
    fmt_duration,
    make_embed,
    parse_duration,
    send_log,
    truncate,
)

MAX_AUDIT_REASON = 500
MAX_TIMEOUT = 28 * 24 * 3600  # Discord's hard limit


class Moderation(commands.Cog, name="Moderation"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------- helpers
    async def _audit_reason(self, ctx: commands.Context, reason: str) -> str:
        return truncate(f"{ctx.author} ({ctx.author.id}): {reason}", MAX_AUDIT_REASON)

    async def _record(self, ctx: commands.Context, target: discord.abc.User, action: str, reason: str | None):
        config = await self.bot.db.get_config(ctx.guild.id)
        await self.bot.db.action_add(
            ctx.guild.id, target.id, ctx.author.id, action, truncate(reason or "", 500)
        )
        await send_log(ctx.guild, config["log_channel"], action_embed(action, target, ctx.author, reason))

    @staticmethod
    def _hierarchy_ok(actor: discord.Member, target: discord.Member) -> bool:
        """Can `actor` act on `target`?"""
        if actor.guild.owner_id == actor.id:
            return True
        return target.top_role < actor.top_role

    # ------------------------------------------------------------------ kick
    @commands.command(name="kick", description="Kick a member from the server.")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided") -> None:
        if not self._hierarchy_ok(ctx.author, member):
            return await ctx.send("That member has an equal or higher role than you.")
        if member.top_role >= ctx.me.top_role and not ctx.me.guild_permissions.administrator:
            return await ctx.send("I can't kick that member — their role is too high.")
        await member.kick(reason=await self._audit_reason(ctx, reason))
        await self._record(ctx, member, "Kick", reason)
        await ctx.send(f"{member.mention} has been kicked. Reason: {truncate(reason, 200)}")

    # ------------------------------------------------------------------- ban
    @commands.command(name="ban", description="Ban a member from the server.")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        delete_days: int = 0,
        reason: str = "No reason provided",
    ) -> None:
        if not 0 <= delete_days <= 7:
            return await ctx.send("`delete_days` must be between 0 and 7.")
        if not self._hierarchy_ok(ctx.author, member):
            return await ctx.send("That member has an equal or higher role than you.")
        if member.top_role >= ctx.me.top_role and not ctx.me.guild_permissions.administrator:
            return await ctx.send("I can't ban that member — their role is too high.")
        await member.ban(
            delete_message_days=delete_days,
            reason=await self._audit_reason(ctx, reason),
        )
        await self._record(ctx, member, "Ban", reason)
        await ctx.send(
            f"{member.mention} has been banned. "
            f"{f'{delete_days} day(s) of messages deleted.' if delete_days else ''}"
        )

    @commands.command(name="unban", description="Unban a user by ID or name.")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user: str, *, reason: str = "No reason provided") -> None:
        raw = user.strip().strip("<>@ ").lstrip("@")
        target_id: int | None = None
        if raw.isdigit():
            target_id = int(raw)
        else:
            lowered = raw.lower()
            async for ban in await ctx.guild.bans():
                if ban.user.name.lower() == lowered or str(ban.user.id) == raw:
                    target_id = ban.user.id
                    break
        if target_id is None:
            return await ctx.send("Couldn't find that user in the ban list.")

        user_object = await self.bot.fetch_user(target_id)
        await ctx.guild.unban(user_object, reason=await self._audit_reason(ctx, reason))
        await self._record(ctx, user_object, "Unban", reason)
        await ctx.send(f"{user_object.mention} has been unbanned.")

    # ------------------------------------------------------------------ mute
    @commands.command(name="mute", aliases=["timeout"], description="Timeout (mute) a member.")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided") -> None:
        if not self._hierarchy_ok(ctx.author, member):
            return await ctx.send("That member has an equal or higher role than you.")
        try:
            seconds = parse_duration(duration)
        except ValueError as exc:
            return await ctx.send(f"{exc}")
        if seconds <= 0:
            return await ctx.send("The duration must be greater than zero.")
        if seconds > MAX_TIMEOUT:
            return await ctx.send(f"Maximum timeout is {fmt_duration(MAX_TIMEOUT)}.")

        until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
        try:
            await member.timeout(until, reason=await self._audit_reason(ctx, reason))
        except discord.HTTPException as exc:
            return await ctx.send(f"Discord rejected that: {exc}")
        await self._record(ctx, member, "Timeout", f"{fmt_duration(seconds)} — {reason}")
        await ctx.send(f"{member.mention} has been timed out for **{fmt_duration(seconds)}**. Reason: {truncate(reason, 200)}")

    @commands.command(name="unmute", description="Remove a timeout from a member.")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided") -> None:
        try:
            await member.timeout(None, reason=await self._audit_reason(ctx, reason))
        except discord.HTTPException as exc:
            return await ctx.send(f"Discord rejected that: {exc}")
        await self._record(ctx, member, "Timeout removed", reason)
        await ctx.send(f"{member.mention} can talk again.")

    # ----------------------------------------------------------------- warns
    @commands.command(name="warn", description="Give a member a formal warning.")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided") -> None:
        await self._record(ctx, member, "Warn", reason)
        embed = make_embed(
            title="You have been warned",
            description=truncate(reason, 1000),
            colour=YELLOW,
        )
        embed.set_footer(text=f"{ctx.guild.name} • action by {ctx.author}")
        try:
            await member.send(embed=embed)
            dm = "They were DM'd."
        except discord.Forbidden:
            dm = "I couldn't DM them (DMs closed)."

        warnings = len(await self.bot.db.actions_for(ctx.guild.id, member.id, "Warn", 100))
        await ctx.send(f"{member.mention} warned (total: **{warnings}**). {dm}")

    @commands.command(name="warnings", aliases=["warns"], description="Show a member's warnings and history.")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def warnings(self, ctx: commands.Context, member: discord.Member) -> None:
        rows = await self.bot.db.actions_for(ctx.guild.id, member.id, None, 20)
        if not rows:
            return await ctx.send(f"{member.mention} has a clean record.")

        embed = make_embed(title=f"Moderation history — {member.display_name}", colour=ctx.bot.config.color_main)
        for index, row in enumerate(rows, start=1):
            when = dt.datetime.fromtimestamp(row["created_at"], dt.timezone.utc).strftime("%d %b %Y")
            embed.add_field(
                name=f"{index}. {row['action']} — {when}",
                value=truncate(row["reason"] or "No reason provided", 200),
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="clearwarnings", aliases=["clearwarns"], description="Wipe a member's record.")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def clearwarnings(self, ctx: commands.Context, member: discord.Member) -> None:
        removed = await self.bot.db.clear_actions(ctx.guild.id, member.id)
        await ctx.send(f"Cleared **{removed}** record(s) for {member.mention}.")
        config = await self.bot.db.get_config(ctx.guild.id)
        await send_log(
            ctx.guild,
            config["log_channel"],
            action_embed("Clear records", member, ctx.author, f"{removed} entries removed"),
        )

    # ----------------------------------------------------------------- purge
    @commands.command(name="purge", aliases=["clear", "bulkdelete"], description="Bulk delete recent messages.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(self, ctx: commands.Context, amount: int = 10, *, member: discord.Member = None) -> None:
        if not 1 <= amount <= 200:
            return await ctx.send("Choose a number between 1 and 200.")

        def can_delete(message: discord.Message) -> bool:
            if message.author.id == ctx.me.id or message.author.id == ctx.author.id:
                return False
            if message.pinned:
                return False
            delta = ctx.message.created_at - message.created_at
            return delta <= dt.timedelta(days=14)  # Discord only lets us delete 14d old

        messages = [m async for m in ctx.channel.history(limit=amount + 30, oldest_first=False)]
        if member is not None:
            messages = [m for m in messages if m.author.id == member.id]
        targets = [m for m in messages if can_delete(m)][:amount]

        if not targets:
            return await ctx.send("Nothing to delete (messages must be under 14 days old and unpinned).")

        await ctx.channel.delete_messages(targets)
        config = await self.bot.db.get_config(ctx.guild.id)
        await send_log(
            ctx.guild,
            config["log_channel"],
            action_embed(
                "Purge",
                member or ctx.author,
                ctx.author,
                f"{len(targets)} message(s) in #{ctx.channel}",
                colour=RED,
            ),
        )
        confirmation = make_embed(description=f"Deleted **{len(targets)}** message(s).", colour=RED)
        await ctx.send(embed=confirmation, delete_after=5)

    # -------------------------------------------------------------- slowmode
    @commands.command(name="slowmode", description="Set this channel's slowmode (0 to disable).")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, duration: str = "0") -> None:
        try:
            seconds = 0 if duration in {"0", "off"} else parse_duration(duration)
        except ValueError as exc:
            return await ctx.send(f"{exc}")
        seconds = max(0, min(seconds, 21600))  # 6 hours max
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"Slowmode set to **{fmt_duration(seconds)}** in {ctx.channel.mention}.")

    # ---------------------------------------------------------- channel lock
    @commands.command(name="lock", description="Stop everyone from talking in a channel.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: discord.TextChannel = None, *, reason: str = "No reason provided") -> None:
        channel = channel or ctx.channel
        await channel.set_permissions(ctx.guild.default_role, send_messages=False)
        await self._record(ctx, ctx.author, f"Lock #{channel.name}", reason)
        await ctx.send(f"{channel.mention} is now locked. Reason: {truncate(reason, 200)}")

    @commands.command(name="unlock", description="Re-open a locked channel.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: discord.TextChannel = None, *, reason: str = "No reason provided") -> None:
        channel = channel or ctx.channel
        await channel.set_permissions(ctx.guild.default_role, send_messages=True)
        await self._record(ctx, ctx.author, f"Unlock #{channel.name}", reason)
        await ctx.send(f"{channel.mention} is open again.")

    # ----------------------------------------------------------------- roles
    @commands.command(name="addrole", description="Give a role to a member.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def addrole(self, ctx: commands.Context, member: discord.Member, role: discord.Role) -> None:
        if role >= ctx.me.top_role:
            return await ctx.send("That role is higher than my highest role.")
        await member.add_roles(role, reason=await self._audit_reason(ctx, "role added"))
        await ctx.send(f"{member.mention} now has {role.mention}.")

    @commands.command(name="removerole", description="Take a role from a member.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def removerole(self, ctx: commands.Context, member: discord.Member, role: discord.Role) -> None:
        await member.remove_roles(role, reason=await self._audit_reason(ctx, "role removed"))
        await ctx.send(f"Removed {role.mention} from {member.mention}.")

    # ---------------------------------------------------------------- roles
    @commands.command(name="roles", description="List every role in the server with its ID.")
    @commands.guild_only()
    async def roles(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        public = [r for r in guild.roles if not r.is_default() and not r.managed]
        managed = [r for r in guild.roles if r.managed]
        self_role = ctx.me.top_role

        def line(role: discord.Role) -> str:
            return f"{role.mention} `{role.id}`"

        normal = [line(r) for r in reversed(public) if r < self_role]
        above = [line(r) for r in reversed(public) if r >= self_role]

        embed = make_embed(
            title=f"Roles in {guild.name}",
            description=(
                f"**Managed by integrations**\n" + ("\n".join(line(r) for r in reversed(managed)) or "None")
                + f"\n\n**Roles I can assign**\n" + ("\n".join(normal) or "None")
                + f"\n\n**Above my highest role**\n" + ("\n".join(above) or "None")
            ),
            colour=self.bot.config.color_main,
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))