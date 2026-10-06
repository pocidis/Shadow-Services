"""Utility commands: ping, uptime, member count, server info, user info."""
from __future__ import annotations

import platform
import time

import discord
from discord.ext import commands

from utils import fmt_duration, truncate, user_tag


class Info(commands.Cog, name="Info"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="ping", aliases=["latency"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ping(self, ctx: commands.Context) -> None:
        """Check the bot's gateway latency."""
        started = time.perf_counter()
        async with ctx.typing():
            pass  # forces a real REST round-trip so the number is honest
        rest = (time.perf_counter() - started) * 1000
        websocket = self.bot.latency * 1000

        bar = self._bar(websocket / 1000)
        colour = (
            self.bot.config.color_success
            if websocket < 300
            else self.bot.config.color_warning
            if websocket < 700
            else self.bot.config.color_error
        )
        embed = discord.Embed(colour=colour)
        embed.description = f"```\n{bar}\n```"
        embed.add_field(name="Websocket", value=f"`{websocket:.0f} ms`", inline=True)
        embed.add_field(name="REST", value=f"`{rest:.0f} ms`", inline=True)
        embed.add_field(name="Shard", value=f"`{ctx.guild.shard_id if ctx.guild else 'DM'}`", inline=True)
        await ctx.send(embed=embed)

    @staticmethod
    def _bar(seconds: float) -> str:
        steps = 10
        filled = max(0, min(steps, int((seconds / 1.25) * steps)))
        return "█" * filled + "░" * (steps - filled)

    @commands.command(name="uptime", aliases=["up"])
    @commands.guild_only()
    async def uptime(self, ctx: commands.Context) -> None:
        """How long the bot has been online."""
        seconds = int(time.time() - self.bot.start_time)
        embed = discord.Embed(
            title="Uptime",
            colour=self.bot.config.color_main,
            description=f"Online for **{fmt_duration(seconds)}**",
        )
        embed.add_field(name="Latency", value=f"`{self.bot.latency * 1000:.0f} ms`", inline=True)
        embed.add_field(name="Guilds", value=f"`{len(self.bot.guilds)}`", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="membercount", aliases=["members", "mc"])
    @commands.guild_only()
    async def membercount(self, ctx: commands.Context) -> None:
        """Total member count, split by humans and bots."""
        guild = ctx.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots = guild.member_count - humans

        embed = discord.Embed(
            title=f"{guild.name}",
            colour=self.bot.config.color_main,
            description=f"**{guild.member_count:,}** members",
        )
        embed.add_field(name="Humans", value=f"{humans:,}", inline=True)
        embed.add_field(name="Bots", value=f"{bots:,}", inline=True)
        if guild.max_members:
            embed.add_field(name="Capacity left", value=f"{guild.max_members - guild.member_count:,}", inline=True)
        embed.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
        await ctx.send(embed=embed)

    @commands.command(name="serverinfo", aliases=["si", "server"])
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        """Detailed information about this server."""
        guild = ctx.guild
        created = guild.created_at.strftime("%d %B %Y")
        embed = discord.Embed(
            title=guild.name,
            description=f"{truncate(guild.description or 'No description', 200) or ''}",
            colour=self.bot.config.color_main,
        )
        if guild.banner:
            embed.set_image(url=guild.banner.url)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        channels = len(guild.channels)
        roles = len(guild.roles) - 1  # exclude @everyone
        emojis = sum(len(e) for e in guild.emojis)
        stickers = len(guild.stickers)

        embed.add_field(name="Owner", value=truncate(str(guild.owner), 40), inline=True)
        embed.add_field(name="Created", value=created, inline=True)
        embed.add_field(name="ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Members", value=f"{guild.member_count:,}", inline=True)
        embed.add_field(name="Channels", value=f"{channels}", inline=True)
        embed.add_field(name="Roles", value=f"{roles}", inline=True)
        embed.add_field(name="Emojis / Stickers", value=f"{emojis} / {stickers}", inline=True)
        embed.add_field(
            name="Verification",
            value=f"Level {guild.verification_level.name.title()}",
            inline=True,
        )
        embed.add_field(
            name="Boosts",
            value=f"{guild.premium_subscription_count or 0} (Tier {guild.premium_tier})",
            inline=True,
        )
        embed.add_field(
            name="Features",
            value=truncate(", ".join(guild.features).replace("_", " ").title() or "None", 1024),
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.command(name="userinfo", aliases=["whois", "ui"])
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context, member: discord.Member = None) -> None:
        """Information about a member."""
        member = member or ctx.author
        roles = [r.mention for r in member.roles[1:]][::-1]
        permissions = member.guild_permissions
        key = [
            "administrator", "manage_guild", "manage_roles", "manage_channels",
            "kick_members", "ban_members", "moderate_members",
        ]
        granted = [p.replace("_", " ").title() for p in key if getattr(permissions, p)]

        embed = discord.Embed(colour=member.colour.value or self.bot.config.color_main)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member", value=user_tag(member), inline=False)
        embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
        embed.add_field(name="Account created", value=member.created_at.strftime("%d %b %Y"), inline=True)
        embed.add_field(name="Joined server", value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "Unknown", inline=True)
        embed.add_field(name="Top role", value=member.top_role.mention, inline=True)
        embed.add_field(name="Roles", value=truncate(", ".join(roles) or "None", 1024), inline=False)
        embed.add_field(name="Key permissions", value=truncate(", ".join(granted) or "None", 1024), inline=False)
        embed.add_field(
            name="Timeout",
            value=f"Until {member.timed_out_at:%d %b %Y %H:%M} UTC" if member.is_timed_out() else "Not timed out",
            inline=True,
        )
        await ctx.send(embed=embed)

    @commands.command(name="botinfo", aliases=["about"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def botinfo(self, ctx: commands.Context) -> None:
        """About this bot and the host it runs on."""
        embed = discord.Embed(
            title=self.bot.user.name,
            description=self.bot.description or "",
            colour=self.bot.config.color_main,
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        uptime = _uptime(int(time.time() - self.bot.start_time))
        embed.add_field(name="Servers", value=f"{len(self.bot.guilds)}", inline=True)
        embed.add_field(name="Users cached", value=f"{sum(g.member_count for g in self.bot.guilds):,}", inline=True)
        embed.add_field(name="Uptime", value=uptime, inline=True)
        embed.add_field(name="Latency", value=f"{self.bot.latency * 1000:.0f} ms", inline=True)
        embed.add_field(name="Discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(name="Platform", value=platform.system(), inline=True)
        embed.add_field(name="Commands", value=f"{len(self.bot.commands)}", inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Info(bot))