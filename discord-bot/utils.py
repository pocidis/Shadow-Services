"""Small helpers shared by every cog."""
from __future__ import annotations

import datetime as dt
import io
import re
from typing import Iterable

import discord

RED = 0xED4245
GREEN = 0x3BA55C
YELLOW = 0xFEE75C
BLUE = 0x5865F2
GREY = 0x9BA5B4

_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([smhdw])")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def make_embed(
    title: str | None = None,
    description: str | None = None,
    colour: int = BLUE,
    **kwargs,
) -> discord.Embed:
    return discord.Embed(title=title, description=description, colour=colour, **kwargs)


def set_footer(embed: discord.Embed, bot: discord.Client | None = None, text: str | None = None) -> discord.Embed:
    embed.set_footer(text=text or (f"{bot.user.name}" if bot else ""))
    embed.timestamp = dt.datetime.now()
    return embed


def user_tag(user: discord.abc.User) -> str:
    return f"{user.mention} (`{user.id}`)"


def truncate(text: str, limit: int = 1024) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def send_log(
    guild: discord.Guild,
    channel_id: int | None,
    embed: discord.Embed | None = None,
    content: str | None = None,
) -> bool:
    """Best-effort delivery to a configured log channel. Never raises."""
    if not channel_id:
        return False
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return False
    try:
        await channel.send(content=content, embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def action_embed(
    action: str,
    target: discord.abc.User,
    moderator: discord.abc.User,
    reason: str | None = None,
    colour: int = GREEN,
    extra: Iterable[tuple[str, str]] = (),
) -> discord.Embed:
    """Standard moderation action log."""
    embed = make_embed(colour=colour)
    embed.add_field(name="Action", value=action, inline=True)
    embed.add_field(name="User", value=user_tag(target), inline=True)
    embed.add_field(name="Moderator", value=moderator.mention, inline=True)
    embed.add_field(name="Reason", value=truncate(reason or "No reason provided", 900), inline=False)
    for name, value in extra:
        embed.add_field(name=name, value=truncate(str(value), 900), inline=True)
    embed.timestamp = utcnow()
    return embed


def event_embed(title: str, colour: int = GREY, *fields: tuple[str, str, bool]) -> discord.Embed:
    """Build a log embed. Each field is (name, value, inline)."""
    embed = make_embed(title=title, colour=colour)
    for name, value, inline in fields:
        embed.add_field(name=name, value=truncate(str(value), 1024), inline=inline)
    embed.timestamp = dt.datetime.now(dt.timezone.utc)
    return embed


def parse_duration(text: str) -> int:
    """`30`, `10m`, `1h30m` -> seconds. Raises ValueError on nonsense."""
    text = (text or "").strip().lower()
    if not text:
        raise ValueError("no duration given")
    if text.isdigit():
        return int(text)
    matches = _DUR_RE.findall(text)
    if not matches:
        raise ValueError("use formats like 30s, 10m, 2h, 1d")
    # Make sure nothing meaningful is left over (e.g. "10mbanana")
    leftover = _DUR_RE.sub("", text)
    if leftover.strip(" ,"):
        raise ValueError("use formats like 30s, 10m, 2h, 1d")
    return int(sum(float(value) * _UNIT[unit] for value, unit in matches))


def fmt_duration(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    if seconds == 0:
        return "0s"
    parts: list[str] = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            value, seconds = divmod(seconds, size)
            parts.append(f"{value}{unit}")
    return " ".join(parts)


def staff_check(member: discord.Member) -> bool:
    """True if the member can normally moderate."""
    perms = member.guild_permissions
    return (
        perms.administrator
        or perms.manage_roles
        or perms.kick_members
        or perms.ban_members
        or perms.moderate_members
    )


async def build_transcript(channel: discord.TextChannel, limit: int = 1000) -> discord.File:
    """Render a plain-text transcript of a channel's recent history."""
    lines: list[str] = [
        f"Transcript for #{channel.name}",
        f"Server: {channel.guild.name} ({channel.guild.id})",
        f"Generated: {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        "=" * 60,
        "",
    ]
    count = 0
    async for message in channel.history(limit=limit, oldest_first=True):
        count += 1
        stamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = f"{message.author} ({message.author.id})"
        content = message.content or "*no text content*"
        lines.append(f"[{stamp}] {author}: {content}")
        for attachment in message.attachments:
            lines.append(f"    [attachment] {attachment.filename} -> {attachment.url}")
    if count == 0:
        lines.append("*(no messages found)*")

    data = "\n".join(lines).encode("utf-8", errors="replace")
    return discord.File(io.BytesIO(data), filename=f"transcript-{channel.id}.txt")