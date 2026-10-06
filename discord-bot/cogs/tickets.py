"""Ticket system: button panel, private channel per user, transcripts on close."""
from __future__ import annotations

import datetime as dt

import discord
from discord.ext import commands

from utils import (
    GREEN,
    YELLOW,
    build_transcript,
    make_embed,
    send_log,
)

TICKET_BUTTON = "ticket_open"


class TicketPanelView(discord.ui.View):
    """Persistent 'open a ticket' button."""

    def __init__(self, cog: "Tickets | None" = None) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.cog is None:
            await interaction.response.send_message("Tickets aren't set up here.", ephemeral=True)
            return False
        config = await self.cog.bot.db.get_config(interaction.guild.id)
        if not config["ticket_category"]:
            await interaction.response.send_message(
                "The ticket system isn't set up on this server.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Open a ticket",
        style=discord.ButtonStyle.primary,
        custom_id=TICKET_BUTTON,
        emoji="\U0001f4dd",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert self.cog is not None
        await self.cog.create_ticket(interaction)


class CloseTicketView(discord.ui.View):
    """Confirm / lock / cancel buttons shown by `!ticket close`."""

    def __init__(self, cog: "Tickets") -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.message: discord.Message | None = None

    def _lock(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Save & delete channel", style=discord.ButtonStyle.danger)
    async def close_and_delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._lock()
        await interaction.response.defer()
        await self.cog.finish_ticket(interaction.channel, interaction.user, delete=True)

    @discord.ui.button(label="Save & keep locked", style=discord.ButtonStyle.secondary)
    async def close_and_lock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._lock()
        await interaction.response.defer()
        await self.cog.finish_ticket(interaction.channel, interaction.user, delete=False)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.success)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._lock()
        await interaction.response.edit_message(content="Cancelled — the ticket stays open.", view=self)

    async def on_timeout(self) -> None:
        self._lock()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Tickets(commands.Cog, name="Tickets"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(TicketPanelView(self))

    async def cog_unload(self) -> None:
        self.bot.remove_view(TicketPanelView(self))

    # ------------------------------------------------------------- internals
    async def create_ticket(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        config = await self.bot.db.get_config(guild.id)

        category = guild.get_channel(config["ticket_category"])
        if category is None:
            await interaction.response.send_message(
                "The ticket category no longer exists — ask an admin to reconfigure it.",
                ephemeral=True,
            )
            return

        for row in await self.bot.db.tickets_open(guild.id):
            if row["owner_id"] == member.id:
                existing = guild.get_channel(row["channel_id"])
                hint = f" You already have one: {existing.mention}." if existing else ""
                await interaction.response.send_message(
                    f"You already have an open ticket.{hint}", ephemeral=True
                )
                return

        await interaction.response.defer(ephemeral=True, thinking=True)

        number = await self.bot.db.next_ticket_number(guild.id)
        overwrites: dict[discord.abc.GuildChannelUnion, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False)
        }
        support_role = guild.get_role(config["ticket_support_role"]) if config["ticket_support_role"] else None
        if support_role is not None:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                attach_files=True,
                read_message_history=True,
            )
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
        )

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{number:04d}-{member.name}"[:100],
                category=category,
                overwrites=overwrites,
                reason=f"ticket opened by {member}",
            )
        except discord.Forbidden:
            await interaction.followup.send("I can't create channels in that category.", ephemeral=True)
            return

        await self.bot.db.ticket_create(guild.id, channel.id, member.id, number, member.display_name)

        welcome = make_embed(
            title=f"Ticket #{number:04d}",
            description=(
                f"Welcome {member.mention}.\n\n"
                "Describe your issue and a staff member will be with you shortly."
            ),
            colour=self.bot.config.color_main,
        )
        welcome.set_footer(text=f"Opened {discord.utils.format_dt(channel.created_at, 'R')}")
        await channel.send(embed=welcome)

        panel = make_embed(
            title="Ticket opened",
            description=(
                f"{member.mention} opened ticket **#{number:04d}** — {channel.mention}\n"
                "Staff: use `!ticket close` when the issue is resolved."
            ),
            colour=GREEN,
        )
        await send_log(guild, config["ticket_log_channel"] or config["log_channel"], panel)
        await interaction.followup.send(f"Ticket opened: {channel.mention}", ephemeral=True)

    async def finish_ticket(
        self,
        channel: discord.TextChannel,
        closer: discord.abc.User,
        delete: bool = True,
    ) -> None:
        guild = channel.guild
        config = await self.bot.db.get_config(guild.id)
        row = await self.bot.db.ticket_by_channel(channel.id)

        owner_id = row["owner_id"] if row else None
        number = int(row["number"]) if row else 0
        owner_text = f"<@{owner_id}>" if owner_id else "unknown"

        # 1. Save the transcript to the ticket log channel.
        try:
            transcript = await build_transcript(channel)
        except Exception:
            transcript = None

        if transcript is not None:
            header = make_embed(
                title=f"Ticket #{number:04d} closed",
                description=f"Channel: #{channel.name}\nOwner: {owner_text}\nClosed by: {closer.mention}",
                colour=YELLOW,
            )
            log_channel = guild.get_channel(config["ticket_log_channel"] or config["log_channel"] or 0)
            if log_channel is not None:
                try:
                    await log_channel.send(embed=header, file=transcript)
                except (discord.Forbidden, discord.HTTPException):
                    pass

        # 2. Close it in the database and tell the owner.
        await self.bot.db.ticket_close(channel.id)
        if owner_id:
            owner = guild.get_member(owner_id)
            if owner is not None:
                try:
                    await owner.send(
                        f"Your ticket **#{number:04d}** in **{guild.name}** was closed by {closer}. "
                        "Need more help? Open a new ticket."
                    )
                except discord.Forbidden:
                    pass

        # 3. Delete or lock the channel.
        if delete:
            await channel.delete(reason=f"ticket closed by {closer}")
        else:
            await channel.set_permissions(guild.default_role, view_channel=False)
            try:
                await channel.edit(name=f"closed-{channel.name}"[:100])
                await channel.send(
                    embed=make_embed(
                        description="This ticket is closed and locked.",
                        colour=self.bot.config.color_success,
                    )
                )
            except discord.Forbidden:
                pass

    # -------------------------------------------------------------- commands
    @commands.command(name="ticketsetup", description="Configure the ticket system.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def ticketsetup(
        self,
        ctx: commands.Context,
        category: discord.CategoryChannel,
        log_channel: discord.TextChannel,
        support_role: discord.Role = None,
    ) -> None:
        await self.bot.db.set_config(
            ctx.guild.id,
            ticket_category=category.id,
            ticket_log_channel=log_channel.id,
            ticket_support_role=support_role.id if support_role else None,
        )
        support_text = support_role.mention if support_role else "none"
        await ctx.send(
            f"Tickets enabled.\nCategory: {category.mention}\n"
            f"Transcripts: {log_channel.mention}\nSupport role: {support_text}"
        )

    @commands.command(name="ticketpanel", description="Post the 'open a ticket' panel.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def ticketpanel(self, ctx: commands.Context, channel: discord.TextChannel = None) -> None:
        config = await self.bot.db.get_config(ctx.guild.id)
        if not config["ticket_category"]:
            return await ctx.send("Run `!ticketsetup #category #logchannel @role` first.")

        channel = channel or ctx.channel
        open_count = len(await self.bot.db.tickets_open(ctx.guild.id))
        embed = make_embed(
            title="Need help?",
            description=(
                "Press the button below to open a private ticket.\n"
                "Only you and the staff team will be able to read it.\n\n"
                f"*Open tickets right now: {open_count}*"
            ),
            colour=self.bot.config.color_main,
        )
        message = await channel.send(embed=embed, view=TicketPanelView(self))
        await self.bot.db.set_config(ctx.guild.id, ticket_panel_message=message.id)
        await ctx.send(f"Panel posted in {channel.mention}.")

    @commands.group(name="ticket", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def ticket(self, ctx: commands.Context) -> None:
        """Manage the current ticket channel."""
        await ctx.send("Subcommands: `close`, `add`, `remove`, `rename`")

    async def _require_ticket(self, ctx: commands.Context) -> bool:
        if await self.bot.db.ticket_by_channel(ctx.channel.id) is None:
            await ctx.send("This isn't a ticket channel.")
            return False
        return True

    @ticket.command(name="close")
    async def ticket_close(self, ctx: commands.Context) -> None:
        """Close this ticket (the transcript is always saved)."""
        if not await self._require_ticket(ctx):
            return
        view = CloseTicketView(self)
        view.message = await ctx.send(
            "Close this ticket? The transcript is saved either way.",
            view=view,
            delete_after=300,
        )

    @ticket.command(name="add")
    async def ticket_add(self, ctx: commands.Context, member: discord.Member) -> None:
        """Add someone to this ticket."""
        if not await self._require_ticket(ctx):
            return
        await ctx.channel.set_permissions(
            member,
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
        )
        await ctx.send(f"{member.mention} added to this ticket.")

    @ticket.command(name="remove")
    async def ticket_remove(self, ctx: commands.Context, member: discord.Member) -> None:
        """Remove someone from this ticket."""
        if not await self._require_ticket(ctx):
            return
        await ctx.channel.set_permissions(member, view_channel=False, send_messages=False)
        await ctx.send(f"{member.mention} removed from this ticket.")

    @ticket.command(name="rename")
    async def ticket_rename(self, ctx: commands.Context, *, name: str) -> None:
        """Rename this ticket channel."""
        if not await self._require_ticket(ctx):
            return
        clean = "".join(c for c in name if c.isalnum() or c in " -_").strip()[:60]
        if not clean:
            return await ctx.send("That name isn't usable.")
        await ctx.channel.edit(name=clean)
        await ctx.send(f"Channel renamed to `{clean}`.")

    @commands.command(name="tickets", description="List open tickets.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def tickets(self, ctx: commands.Context) -> None:
        rows = await self.bot.db.tickets_open(ctx.guild.id)
        if not rows:
            return await ctx.send("No open tickets.")

        embed = make_embed(title=f"Open tickets ({len(rows)})", colour=self.bot.config.color_main)
        for row in rows:
            channel = ctx.guild.get_channel(row["channel_id"])
            where = channel.mention if channel else "*channel deleted*"
            opened = dt.datetime.fromtimestamp(row["created_at"], dt.timezone.utc)
            subject = row["subject"] or "no subject"
            embed.add_field(
                name=f"#{int(row['number']):04d} — {subject}",
                value=f"Owner: <@{row['owner_id']}>\nChannel: {where}\nOpened: {discord.utils.format_dt(opened, 'R')}",
                inline=False,
            )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))