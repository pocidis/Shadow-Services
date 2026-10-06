"""Server logging: message deletes/edits, joins, leaves, bans, channels, roles."""
from __future__ import annotations

import datetime as dt

import discord
from discord.ext import commands

from utils import GREY, RED, YELLOW, event_embed, send_log, truncate


class Logs(commands.Cog, name="Logs"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------- internals
    async def _log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        config = await self.bot.db.get_config(guild.id)
        await send_log(guild, config["log_channel"], embed)

    async def _log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        config = await self.bot.db.get_config(guild.id)
        if not config["log_channel"]:
            return None
        channel = guild.get_channel(config["log_channel"])
        return channel if isinstance(channel, discord.TextChannel) else None

    # -------------------------------------------------------------- commands
    @commands.group(name="logs", aliases=["log"], invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def logs(self, ctx: commands.Context) -> None:
        """Show where server logs are sent."""
        config = await self.bot.db.get_config(ctx.guild.id)
        channel = ctx.guild.get_channel(config["log_channel"]) if config["log_channel"] else None
        if channel is None:
            await ctx.send(
                "Logging is off. Set it up with `!logs set #log-channel` "
                "(or `!logs logs off` to disable it)."
            )
        else:
            await ctx.send(f"Server logs are going to {channel.mention}.")

    @logs.command(name="set")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def logs_set(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Send server logs to a channel."""
        await self.bot.db.set_config(ctx.guild.id, log_channel=channel.id)
        await ctx.send(f"Server logs will be sent to {channel.mention}.")

    @logs.command(name="off")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def logs_off(self, ctx: commands.Context) -> None:
        """Stop server logging."""
        await self.bot.db.set_config(ctx.guild.id, log_channel=None)
        await ctx.send("Server logging disabled.")

    @logs.command(name="test")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def logs_test(self, ctx: commands.Context) -> None:
        """Post a test entry to the log channel."""
        embed = event_embed(
            "Logging test",
            GREY,
            ("Message", "If you can read this, logging works.", False),
            ("By", ctx.author.mention, False),
        )
        delivered = await send_log(ctx.guild, (await self.bot.db.get_config(ctx.guild.id))["log_channel"], embed)
        await ctx.send("Test sent." if delivered else "No log channel is set — run `!logs set #channel`.")

    # --------------------------------------------------------------- events
    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.cached_message is not None:
            return  # already handled by on_message (cog-level listener)
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(payload.channel_id)
        author_name = "*unknown*"
        content = "*message not cached*"
        if isinstance(channel, discord.TextChannel):
            cached = channel.messages.get(payload.message_id)
            if cached is not None:
                author_name = f"{cached.author} (`{cached.author.id}`)"
                content = truncate(cached.content or "*no content*")
        embed = event_embed(
            "Message deleted",
            RED,
            ("Channel", channel.mention if channel else "unknown", True),
            ("Author", author_name, True),
            ("Content", content, False),
        )
        await self._log(guild, embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        guild = message.guild
        if guild is None or message.author.bot:
            return
        embed = event_embed(
            "Message deleted",
            RED,
            ("Channel", message.channel.mention, True),
            ("Author", f"{message.author} (`{message.author.id}`)", True),
            ("Content", truncate(message.content or "*no content*"), False),
            ("Attachments", str(len(message.attachments)), True),
        )
        await self._log(guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.author.bot or before.content == after.content:
            return
        embed = event_embed(
            "Message edited",
            YELLOW,
            ("Channel", after.channel.mention, True),
            ("Author", after.author.mention, True),
            ("Before", truncate(before.content or "*empty*", 800), False),
            ("After", truncate(after.content or "*empty*", 800), False),
            ("Link", after.jump_url, False),
        )
        await self._log(after.guild, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        account_age = (dt.datetime.now(dt.timezone.utc) - member.created_at).days
        embed = event_embed(
            "Member joined",
            GREY,
            ("Member", f"{member} (`{member.id}`)", False),
            ("Account created", member.created_at.strftime("%d %b %Y"), True),
            ("Member count", str(member.guild.member_count), True),
        )
        if account_age < 7:
            embed.add_field(name="⚠️ New account", value=f"Only **{account_age} day(s)** old!", inline=False)
        await self._log(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        embed = event_embed(
            "Member left",
            RED,
            ("Member", f"{member} (`{member.id}`)", False),
            ("Joined", member.joined_at.strftime("%d %b %Y") if member.joined_at else "unknown", True),
            ("Member count", str(member.guild.member_count), True),
        )
        await self._log(member.guild, embed)

    @commands.Cog.listener()
    async def on_guild_ban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = event_embed(
            "Member banned",
            RED,
            ("User", f"{user} (`{user.id}`)", False),
            ("Note", "Moderator details are in the server audit log", True),
        )
        await self._log(guild, embed)

    @commands.Cog.listener()
    async def on_guild_unban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = event_embed(
            "Member unbanned",
            GREY,
            ("User", f"{user} (`{user.id}`)", False),
        )
        await self._log(guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        embed = event_embed("Channel created", GREY, ("Channel", getattr(channel, "mention", channel.name), False))
        await self._log(channel.guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        embed = event_embed("Channel deleted", RED, ("Channel", channel.name, False))
        await self._log(channel.guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        embed = event_embed("Role created", GREY, ("Role", role.mention, False))
        await self._log(role.guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        embed = event_embed("Role deleted", RED, ("Role", role.name, False))
        await self._log(role.guild, embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Logs(bot))