"""Welcome / leave messages and automatic role on join."""
from __future__ import annotations

import discord
from discord.ext import commands

from utils import GREY, GREEN, make_embed, set_footer

PLACEHOLDERS = (
    "{user} {username} {tag} {id} {server} {membercount}"
)

DEFAULT_WELCOME = (
    "Welcome {user} to **{server}**! You are member **#{membercount}**."
)


def render(template: str, member: discord.Member, guild: discord.Guild) -> str:
    return (
        template.replace("{user}", member.mention)
        .replace("{username}", member.display_name)
        .replace("{tag}", str(member))
        .replace("{id}", str(member.id))
        .replace("{server}", guild.name)
        .replace("{membercount}", str(guild.member_count))
    )


class Welcome(commands.Cog, name="Welcome"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -------------------------------------------------------------- commands
    @commands.group(name="setwelcome", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setwelcome(self, ctx: commands.Context, channel: discord.TextChannel = None) -> None:
        """Configure or disable the welcome message."""
        config = await self.bot.db.get_config(ctx.guild.id)
        if channel is None:
            if not config["welcome_channel"]:
                return await ctx.send("No welcome channel is configured. Usage: `!setwelcome #channel`")
            dm_text = config["welcome_dm"] or "off"
            auto_role_id = config["autorole_id"]
            auto_role_text = "<@&%s>" % auto_role_id if auto_role_id else "off"
            await ctx.send(
                f"Welcome channel: <#{config['welcome_channel']}>\n"
                f"Welcome DM: {dm_text}\n"
                f"Auto-role: {auto_role_text}\n"
                f"Placeholders: `{PLACEHOLDERS}`"
            )
            return

        await self.bot.db.set_config(
            ctx.guild.id,
            welcome_channel=channel.id,
            welcome_message=config["welcome_message"] or DEFAULT_WELCOME,
        )
        await ctx.send(f"Welcome messages will be sent in {channel.mention}.")

    @setwelcome.command(name="message")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setwelcome_message(self, ctx: commands.Context, *, message: str) -> None:
        """Set the welcome text. Placeholders: {user} {username} {tag} {id} {server} {membercount}"""
        await self.bot.db.set_config(ctx.guild.id, welcome_message=message)
        preview = render(message, ctx.author, ctx.guild)
        embed = make_embed(description=f"Preview:\n{preview}", colour=GREEN)
        await ctx.send(embed=embed)

    @setwelcome.command(name="dm")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setwelcome_dm(self, ctx: commands.Context, *, message: str = "Welcome to {server}!") -> None:
        """Send a DM to new members (their DMs may be closed)."""
        await self.bot.db.set_config(ctx.guild.id, welcome_dm=message)
        await ctx.send("New members will get a DM with that text.")

    @setwelcome.command(name="off")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setwelcome_off(self, ctx: commands.Context) -> None:
        """Disable welcome messages."""
        await self.bot.db.set_config(ctx.guild.id, welcome_channel=None)
        await ctx.send("Welcome messages disabled.")

    @setwelcome.command(name="placeholders")
    async def setwelcome_placeholders(self, ctx: commands.Context) -> None:
        """Show the available placeholders."""
        await ctx.send(f"Available placeholders:\n`{PLACEHOLDERS}`")

    @commands.command(name="setleave", description="Configure or disable the leave message.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setleave(self, ctx: commands.Context, *, message: str = None) -> None:
        config = await self.bot.db.get_config(ctx.guild.id)
        if message is None:
            await ctx.send(
                f"Current leave message: `{config['leave_message'] or 'disabled'}`\n"
                f"Placeholders: `{PLACEHOLDERS}`"
            )
            return
        await self.bot.db.set_config(ctx.guild.id, leave_message=message)
        await ctx.send(f"Leave message set to: {message}")

    @commands.command(name="autorole", description="Manage the role given automatically on join.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def autorole(self, ctx: commands.Context, action: str, role: discord.Role = None) -> None:
        """Usage: `!autorole add|remove|show`"""
        action = action.lower()
        config = await self.bot.db.get_config(ctx.guild.id)

        if action in {"show", "list", "status"}:
            if config["autorole_id"]:
                role_obj = ctx.guild.get_role(config["autorole_id"])
                text = role_obj.mention if role_obj else "configured role no longer exists"
            else:
                text = "No auto-role configured."
            await ctx.send(f"Auto-role: {text}")
            return

        if action == "add":
            if role is None:
                return await ctx.send("Usage: `!autorole add @Role`")
            if role >= ctx.me.top_role:
                return await ctx.send("That role is above my highest role — move me up first.")
            await self.bot.db.set_config(ctx.guild.id, autorole_id=role.id)
            await ctx.send(f"New members will automatically receive {role.mention}.")
        elif action == "remove":
            await self.bot.db.set_config(ctx.guild.id, autorole_id=None)
            await ctx.send("Auto-role removed.")
        else:
            await ctx.send("Usage: `!autorole add|remove|show`")

    @commands.command(name="testwelcome", description="Preview the welcome message.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def testwelcome(self, ctx: commands.Context) -> None:
        config = await self.bot.db.get_config(ctx.guild.id)
        template = config["welcome_message"] or DEFAULT_WELCOME
        embed = make_embed(description=render(template, ctx.author, ctx.guild), colour=GREEN)
        set_footer(embed, self.bot, "This is a preview")
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------- events
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        config = await self.bot.db.get_config(member.guild.id)

        if config["autorole_id"]:
            role = member.guild.get_role(config["autorole_id"])
            if role:
                try:
                    await member.add_roles(role, reason="auto-role on join")
                except discord.Forbidden:
                    pass

        if config["welcome_channel"]:
            channel = member.guild.get_channel(config["welcome_channel"])
            if channel:
                embed = make_embed(
                    title=f"Welcome to {member.guild.name}",
                    description=render(config["welcome_message"] or DEFAULT_WELCOME, member, member.guild),
                    colour=GREEN,
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                set_footer(embed, self.bot, f"Member #{member.guild.member_count}")
                try:
                    await channel.send(embed=embed)
                except discord.Forbidden:
                    pass

        if config["welcome_dm"]:
            embed = make_embed(
                title=f"Welcome to {member.guild.name}",
                description=render(config["welcome_dm"], member, member.guild),
                colour=self.bot.config.color_main,
            )
            try:
                await member.send(embed=embed)
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        config = await self.bot.db.get_config(member.guild.id)
        if not config["welcome_channel"] or not config["leave_message"]:
            return
        channel = member.guild.get_channel(config["welcome_channel"])
        if not channel:
            return
        embed = make_embed(
            title="Member left",
            description=render(config["leave_message"], member, member.guild),
            colour=GREY,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))