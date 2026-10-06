"""Security system: anti-spam, anti-raid, link/mention blocking, word filter,
lockdown mode and a global user blacklist."""
from __future__ import annotations

import collections
import datetime as dt
import json
import re
import time

import discord
from discord.ext import commands

from utils import (
    RED,
    YELLOW,
    action_embed,
    make_embed,
    parse_duration,
    send_log,
    staff_check,
    truncate,
)

LINK_RE = re.compile(
    r"(?:https?://\S+|discord(?:app)?\.com/invite/\S+|discord\.gg/\S+)",
    re.IGNORECASE,
)

SPAM_ACTIONS = {"delete", "timeout", "kick"}


def _on_off(value: int | None) -> str:
    return "on" if value else "off"


class Security(commands.Cog, name="Security"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # (guild_id, user_id) -> timestamps of recent messages
        self.hits: dict[tuple[int, int], collections.deque[float]] = collections.defaultdict(
            lambda: collections.deque(maxlen=30)
        )
        # (guild_id, user_id) -> last content, for duplicate-message detection
        self.last_content: dict[tuple[int, int], str] = {}

    # ------------------------------------------------------------- internals
    async def _alert(self, guild: discord.Guild, embed: discord.Embed) -> None:
        config = await self.bot.db.get_config(guild.id)
        await send_log(guild, config["log_channel"], embed)

    async def _delete_recent(self, channel: discord.TextChannel, user_id: int, keep: int = 0) -> int:
        try:
            history = [
                message
                async for message in channel.history(limit=25)
                if message.author.id == user_id
            ]
        except (discord.Forbidden, discord.HTTPException):
            return 0
        if keep:
            history = history[keep:]
        if not history:
            return 0
        try:
            await channel.delete_messages(history[:20])
        except (discord.Forbidden, discord.HTTPException):
            return 0
        return min(len(history), 20)

    async def _punish(
        self,
        message: discord.Message,
        config: dict,
        reason: str,
        action: str | None = None,
    ) -> None:
        """Apply the configured anti-spam action to a spammer."""
        guild = message.guild
        member = message.author
        action = action or config["antispam_action"] or "delete"

        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        note = ""
        if action == "timeout":
            try:
                await member.timeout(dt.timedelta(minutes=10), reason=f"Anti-spam: {reason}")
                note = "Timed out for 10 minutes."
            except (discord.Forbidden, discord.HTTPException):
                note = "I couldn't time them out."
        elif action == "kick":
            try:
                await member.kick(reason=f"Anti-spam: {reason}")
                note = "Kicked."
            except (discord.Forbidden, discord.HTTPException):
                note = "I couldn't kick them."

        self.hits[(guild.id, member.id)].clear()

        embed = action_embed("Anti-spam", member, self.bot.user, reason, colour=RED)
        embed.add_field(name="Action taken", value=f"{action} — {note}", inline=False)
        await self._alert(guild, embed)

        dm = make_embed(
            title="You were moderated",
            description=(
                f"**{reason}**\n"
                f"This channel: {message.channel.mention}\n"
                f"Action: {note}"
            ),
            colour=RED,
        )
        try:
            await member.send(embed=dm)
        except discord.Forbidden:
            pass

    # -------------------------------------------------------------- commands
    @commands.group(name="security", aliases=["sec"], invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def security(self, ctx: commands.Context) -> None:
        """Show the current security settings."""
        config = await self.bot.db.get_config(ctx.guild.id)
        words = json.loads(config["word_filter"] or "[]")

        on_off = _on_off

        embed = make_embed(title="Security status", colour=self.bot.config.color_main)
        embed.add_field(
            name="Anti-spam",
            value=(
                f"{on_off(config['antispam_enabled'])} • "
                f"{config['antispam_limit']} messages / {config['antispam_window']}s • "
                f"action: {config['antispam_action']}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Anti-raid",
            value=(
                f"{on_off(config['antiraid_enabled'])} • "
                f"{config['antiraid_threshold']} joins / {config['antiraid_window']}s"
            ),
            inline=False,
        )
        embed.add_field(name="Link blocking", value=on_off(config["linkblock_enabled"]), inline=True)
        embed.add_field(name="Mention blocking", value=on_off(config["mentionblock_enabled"]), inline=True)
        embed.add_field(name="Lockdown", value=on_off(config["lockdown"]), inline=True)
        embed.add_field(
            name="Word filter",
            value=truncate(", ".join(words) if words else "empty", 900),
            inline=False,
        )
        await ctx.send(embed=embed)

    @security.command(name="antispam")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def sec_antispam(
        self,
        ctx: commands.Context,
        state: str,
        limit: int = 6,
        window: str = "8",
        action: str = "delete",
    ) -> None:
        """Anti-spam: `!sec antispam on|off [limit] [window] [action]`

        action = delete | timeout | kick
        """
        state = state.lower()
        if state in {"on", "enable", "true"}:
            action = action.lower()
            if action not in SPAM_ACTIONS:
                return await ctx.send(f"Action must be one of: {', '.join(sorted(SPAM_ACTIONS))}")
            try:
                seconds = parse_duration(window)
            except ValueError as exc:
                return await ctx.send(f"{exc}")
            limit = max(2, min(limit, 20))
            seconds = max(2, min(seconds, 120))
            await self.bot.db.set_config(
                ctx.guild.id,
                antispam_enabled=1,
                antispam_limit=limit,
                antispam_window=seconds,
                antispam_action=action,
            )
            await ctx.send(
                f"Anti-spam **on**: more than {limit} messages in {seconds}s triggers `{action}`."
            )
            return
        if state in {"off", "disable", "false"}:
            await self.bot.db.set_config(ctx.guild.id, antispam_enabled=0)
            await ctx.send("Anti-spam disabled.")
            return
        await ctx.send("Usage: `!sec antispam on [limit] [window] [action]` or `!sec antispam off`")

    @security.command(name="antiraid")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def sec_antiraid(self, ctx: commands.Context, state: str, threshold: int = 8, window: str = "15") -> None:
        """Anti-raid: `!sec antiraid on|off [joins] [window]`"""
        state = state.lower()
        if state in {"on", "enable", "true"}:
            try:
                seconds = parse_duration(window)
            except ValueError as exc:
                return await ctx.send(f"{exc}")
            threshold = max(2, min(threshold, 50))
            await self.bot.db.set_config(
                ctx.guild.id,
                antiraid_enabled=1,
                antiraid_threshold=threshold,
                antiraid_window=max(5, min(seconds, 300)),
            )
            await ctx.send(
                f"Anti-raid **on**: more than {threshold} joins in {seconds}s raises an alert. "
                "Run `!sec panic` to kick the recent joiners."
            )
            return
        if state in {"off", "disable", "false"}:
            await self.bot.db.set_config(ctx.guild.id, antiraid_enabled=0)
            await ctx.send("Anti-raid disabled.")
            return
        await ctx.send("Usage: `!sec antiraid on [joins] [window]` or `!sec antiraid off`")

    @security.command(name="linkblock", aliases=["links"])
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def sec_linkblock(self, ctx: commands.Context, state: str, *channels: discord.TextChannel) -> None:
        """`!sec linkblock on|off` — or list exceptions: `!sec linkblock exceptions`"""
        state = state.lower()
        config = await self.bot.db.get_config(ctx.guild.id)
        current = set(json.loads(config["linkblock_ignored"] or "[]"))

        if state in {"exceptions", "list"}:
            text = ", ".join(f"<#{cid}>" for cid in sorted(current)) or "none"
            await ctx.send(f"Channels where links are allowed: {text}")
            return
        if state in {"allow", "ignore"}:
            for channel in channels:
                current.add(channel.id)
            await self.bot.db.set_config(ctx.guild.id, linkblock_ignored=json.dumps(sorted(current)))
            await ctx.send(f"Links allowed in: {', '.join(c.mention for c in channels)}")
            return
        if state in {"block", "disallow"}:
            for channel in channels:
                current.discard(channel.id)
            await self.bot.db.set_config(ctx.guild.id, linkblock_ignored=json.dumps(sorted(current)))
            await ctx.send("Those channels no longer have a link exception.")
            return
        if state in {"on", "enable", "true"}:
            await self.bot.db.set_config(ctx.guild.id, linkblock_enabled=1)
            await ctx.send("Links are now blocked everywhere (staff and listed channels exempt).")
            return
        if state in {"off", "disable", "false"}:
            await self.bot.db.set_config(ctx.guild.id, linkblock_enabled=0)
            await ctx.send("Link blocking disabled.")
            return
        await ctx.send(
            "Usage: `!sec linkblock on|off`, `!sec linkblock allow #chan`, "
            "`!sec linkblock block #chan`, `!sec linkblock exceptions`"
        )

    @security.command(name="mentionblock", aliases=["mentions"])
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def sec_mentionblock(self, ctx: commands.Context, state: str) -> None:
        """`!sec mentionblock on|off` — blocks @everyone and mass mentions."""
        state = state.lower()
        if state in {"on", "enable", "true"}:
            await self.bot.db.set_config(ctx.guild.id, mentionblock_enabled=1)
            await ctx.send("Mass-mention protection enabled.")
        elif state in {"off", "disable", "false"}:
            await self.bot.db.set_config(ctx.guild.id, mentionblock_enabled=0)
            await ctx.send("Mass-mention protection disabled.")
        else:
            await ctx.send("Usage: `!sec mentionblock on|off`")

    @security.command(name="filter", aliases=["wordfilter"])
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def sec_filter(self, ctx: commands.Context, action: str, *words: str) -> None:
        """`!sec filter add|remove|clear|show word [word...]`"""
        config = await self.bot.db.get_config(ctx.guild.id)
        current: list[str] = json.loads(config["word_filter"] or "[]")
        action = action.lower()

        if action in {"show", "list"}:
            await ctx.send(
                f"Filtered words ({len(current)}): {', '.join(current) if current else 'none'}"
            )
            return
        if action == "add":
            new = [w.lower() for w in words if w and w.lower() not in current]
            if not new:
                return await ctx.send("Give me at least one new word to add.")
            current.extend(new)
        elif action == "remove":
            current = [w for w in current if w not in {x.lower() for x in words}]
        elif action == "clear":
            current = []
        else:
            return await ctx.send("Usage: `!sec filter add|remove|clear|show word [word...]`")

        await self.bot.db.set_config(ctx.guild.id, word_filter=json.dumps(sorted(set(current))))
        await ctx.send(f"Word filter updated ({len(set(current))} word(s)).")

    @security.command(name="lockdown")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True, administrator=True)
    async def sec_lockdown(self, ctx: commands.Context, state: str) -> None:
        """`!sec lockdown on|off` — staff can still talk; everyone else is silenced."""
        state = state.lower()
        if state in {"on", "enable", "true"}:
            await self.bot.db.set_config(ctx.guild.id, lockdown=1)
            await ctx.send(
                "**Lockdown is on.** Only members with manage_messages/manage_roles can talk. "
                "Turn it off with `!sec lockdown off`."
            )
        elif state in {"off", "disable", "false"}:
            await self.bot.db.set_config(ctx.guild.id, lockdown=0)
            await ctx.send("Lockdown lifted.")
        else:
            await ctx.send("Usage: `!sec lockdown on|off`")

    @security.command(name="blacklist", aliases=["bl"])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def sec_blacklist(
        self,
        ctx: commands.Context,
        action: str,
        user: discord.User = None,
        *,
        reason: str = "No reason given",
    ) -> None:
        """`!sec blacklist add|remove|list [@user] [reason]`"""
        action = action.lower()
        if action in {"list", "show"}:
            rows = await self.bot.db.blacklist_all()
            if not rows:
                return await ctx.send("The blacklist is empty.")
            text = "\n".join(
                f"`{row['user_id']}` — added by <@{row['added_by']}> · {row['reason'] or 'no reason'}"
                for row in rows[:25]
            )
            await ctx.send(f"Blacklisted users ({len(rows)}):\n{text}")
            return
        if user is None:
            return await ctx.send("Usage: `!sec blacklist add @user [reason]` or `!sec blacklist remove @user`")

        if action == "add":
            await self.bot.db.blacklist_add(user.id, ctx.author.id, reason)
            await ctx.send(f"{user.mention} is blacklisted (they'll be kicked on join).")
            config = await self.bot.db.get_config(ctx.guild.id)
            await send_log(
                ctx.guild,
                config["log_channel"],
                action_embed("Blacklist add", user, ctx.author, reason, colour=RED),
            )
        elif action == "remove":
            await self.bot.db.blacklist_remove(user.id)
            await ctx.send(f"{user.mention} removed from the blacklist.")
        else:
            await ctx.send("Usage: `!sec blacklist add|remove|list [@user]`")

    @security.command(name="panic", description="Kick everyone who joined in the last N minutes.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True, kick_members=True)
    async def sec_panic(self, ctx: commands.Context, minutes: int = 10) -> None:
        """Kicks recent joiners (bots and staff exempt). Use during a raid."""
        minutes = max(1, min(minutes, 1440))
        user_ids = await self.bot.db.raid_recent_users(ctx.guild.id, minutes * 60)
        kicked = 0
        skipped = 0
        for user_id in reversed(user_ids):  # newest first
            member = ctx.guild.get_member(user_id)
            if member is None or member.bot:
                continue
            if staff_check(member):
                skipped += 1
                continue
            try:
                await member.kick(reason=f"anti-raid panic by {ctx.author}")
                kicked += 1
            except (discord.Forbidden, discord.HTTPException):
                continue

        await ctx.send(
            f"Kicked **{kicked}** recent joiner(s) from the last {minutes} minute(s)"
            f"{f' (skipped {skipped} staff member(s))' if skipped else ''}."
        )
        await self._alert(
            ctx.guild,
            action_embed(
                "Panic",
                ctx.author,
                self.bot.user,
                f"{kicked} kicked, {skipped} skipped",
                colour=RED,
            ),
        )

    # --------------------------------------------------------------- events
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if isinstance(message.channel, discord.DMChannel):
            return

        guild = message.guild
        config = await self.bot.db.get_config(guild.id)
        member = message.author
        is_staff = staff_check(member)
        key = (guild.id, member.id)

        # 1. Lockdown — non-staff cannot talk at all.
        if config["lockdown"] and not is_staff:
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        # 2. Anti-spam.
        if config["antispam_enabled"] and not is_staff:
            now = time.time()
            window = int(config["antispam_window"] or 8)
            limit = int(config["antispam_limit"] or 6)
            bucket = self.hits[key]
            bucket.append(now)
            while bucket and now - bucket[0] > window:
                bucket.popleft()

            duplicate = self.last_content.get(key) == message.content
            self.last_content[key] = message.content

            if len(bucket) >= limit or (duplicate and len(bucket) >= 3):
                reason = (
                    f"{len(bucket)} messages in {window}s"
                    if len(bucket) >= limit
                    else "repeated the same message 3 times"
                )
                await self._punish(message, config, reason)
                return

        # 3. Word filter.
        words = json.loads(config["word_filter"] or "[]")
        if words and not is_staff:
            lowered = message.content.lower()
            hits = [w for w in words if w and w in lowered]
            if hits:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await self._alert(
                    guild,
                    action_embed(
                        "Word filter",
                        member,
                        self.bot.user,
                        f"Matched: {', '.join(hits)}",
                        colour=YELLOW,
                    ),
                )
                return

        # 4. Link blocking.
        if config["linkblock_enabled"] and not is_staff:
            ignored = set(json.loads(config["linkblock_ignored"] or "[]"))
            if message.channel.id not in ignored and LINK_RE.search(message.content or ""):
                try:
                    await message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await self._alert(
                    guild,
                    action_embed(
                        "Link blocked",
                        member,
                        self.bot.user,
                        f"Channel: {message.channel.mention}",
                        colour=YELLOW,
                    ),
                )
                return

        # 5. Mass mentions.
        if config["mentionblock_enabled"] and not is_staff:
            mass_mention = (
                message.mention_everyone or bool(message.role_mentions) or len(message.mentions) > 5
            )
            if mass_mention:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await self._alert(
                    guild,
                    action_embed(
                        "Mass mention blocked",
                        member,
                        self.bot.user,
                        f"Channel: {message.channel.mention}",
                        colour=YELLOW,
                    ),
                )
                return

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        if member.bot:
            return

        if await self.bot.db.blacklist_contains(member.id):
            try:
                await member.kick(reason="blacklisted")
            except (discord.Forbidden, discord.HTTPException):
                pass
            config = await self.bot.db.get_config(guild.id)
            await send_log(
                guild,
                config["log_channel"],
                action_embed("Blacklisted user removed", member, self.bot.user, "kicked on join", colour=RED),
            )
            return

        config = await self.bot.db.get_config(guild.id)
        if not config["antiraid_enabled"]:
            return

        window = int(config["antiraid_window"] or 15)
        threshold = int(config["antiraid_threshold"] or 8)
        recent = await self.bot.db.raid_join(guild.id, member.id)

        if recent > threshold:
            age = (dt.datetime.now(dt.timezone.utc) - member.created_at).days
            await send_log(
                guild,
                config["log_channel"],
                action_embed(
                    "Possible raid",
                    member,
                    self.bot.user,
                    f"{recent} joins in {window}s (threshold {threshold})",
                    colour=RED,
                ).add_field(name="Account age", value=f"{age} day(s)", inline=True),
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Security(bot))