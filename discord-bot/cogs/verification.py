"""Verification system.

Two modes:
  * ``captcha`` - members answer a small maths question (wrong answers are counted)
  * ``button``  - a single click is enough
"""
from __future__ import annotations

import datetime as dt
import random
import secrets

import discord
from discord.ext import commands

from utils import GREEN, RED, YELLOW, action_embed, make_embed, send_log, set_footer

MAX_ATTEMPTS = 5
TIMEOUT_MINUTES = 30
VERIFY_BUTTON = "verify_start"


def _make_question() -> tuple[str, int, list[int]]:
    """Build one maths question.

    Returns ``(question_text, answer, options)`` where options holds three
    choices with exactly one correct value.
    """
    kind = random.choice(("add", "sub", "mul"))
    if kind == "add":
        a, b = random.randint(3, 40), random.randint(2, 25)
        question, answer = f"{a} + {b}", a + b
    elif kind == "sub":
        a = random.randint(15, 45)
        b = random.randint(2, a - 1)  # keep the answer positive
        question, answer = f"{a} - {b}", a - b
    else:
        a, b = random.randint(2, 12), random.randint(2, 12)
        question, answer = f"{a} x {b}", a * b

    options = {answer}
    for _ in range(50):
        if len(options) >= 3:
            break
        options.add(max(0, answer + random.randint(-10, 10)))
    # Deterministic fallback so the loop can never spin forever.
    while len(options) < 3:
        options.add(answer + len(options) + 1)

    shuffled = list(options)
    random.shuffle(shuffled)
    return question, answer, shuffled


class CaptchaButton(discord.ui.Button):
    """One answer choice. The custom_id carries nonce + value so several
    captchas can be on screen at the same time without interfering."""

    def __init__(self, cog: "Verification", nonce: str, owner_id: int, value: int) -> None:
        super().__init__(
            label=str(value),
            style=discord.ButtonStyle.primary,
            custom_id=f"cap:{nonce}:{value}",
        )
        self.cog = cog
        self.nonce = nonce
        self.owner_id = owner_id
        self.answer_value = value

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "That verification question isn't yours — press Verify yourself to get your own.",
                ephemeral=True,
            )
            return
        await self.cog.handle_answer(interaction, self.nonce, self.answer_value)


class CaptchaView(discord.ui.View):
    """Ephemeral view holding the three answer buttons for one attempt."""

    def __init__(self, cog: "Verification", nonce: str, owner_id: int, options: list[int]) -> None:
        super().__init__(timeout=None)
        for option in options:
            self.add_item(CaptchaButton(cog, nonce, owner_id, option))


class VerifyView(discord.ui.View):
    """Persistent panel. The custom_id is stable so old panels keep working
    after a restart."""

    def __init__(self, cog: "Verification | None" = None) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.cog is None or not (await self.cog.bot.db.get_config(interaction.guild.id))["verification_role"]:
            await interaction.response.send_message(
                "Verification isn't set up on this server.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Verify me", style=discord.ButtonStyle.success, custom_id=VERIFY_BUTTON, emoji="\u2705"
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert self.cog is not None
        await self.cog.start_verification(interaction)


class Verification(commands.Cog, name="Verification"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # nonce -> {"answer": int, "owner": int, "guild": int, "question": str}
        self.captchas: dict[str, dict] = {}

    async def cog_load(self) -> None:
        # Re-attach the persistent view so existing panels keep working.
        self.bot.add_view(VerifyView(self))

    async def cog_unload(self) -> None:
        self.bot.remove_view(VerifyView(self))
        self.captchas.clear()

    # ------------------------------------------------------------- internals
    async def _grant(
        self,
        member: discord.Member,
        role_id: int | None,
        actor: discord.abc.User | None = None,
    ) -> str:
        config = await self.bot.db.get_config(member.guild.id)
        if not role_id:
            return "Verification isn't configured on this server."

        role = member.guild.get_role(role_id)
        if role is None:
            return "The verification role no longer exists — tell an admin."
        if role in member.roles:
            return f"{member.mention}, you are already verified."
        if member.top_role >= self.bot.me.top_role:
            return "I can't assign that role — it is above my highest role."

        try:
            await member.add_roles(role, reason="verification completed")
        except discord.Forbidden:
            return "I can't assign that role. Check my permissions and role position."
        await self.bot.db.verify_reset(member.guild.id, member.id)
        await send_log(
            member.guild,
            config["log_channel"],
            action_embed("Verified", member, actor or self.bot.user, "captcha passed", colour=GREEN),
        )
        return f"Success — you're verified, {member.mention}. Welcome!"

    async def _ask_question(self, interaction: discord.Interaction, guild_id: int, owner_id: int) -> None:
        question, answer, options = _make_question()
        nonce = secrets.token_hex(6)
        self.captchas[nonce] = {
            "answer": answer,
            "owner": owner_id,
            "guild": guild_id,
            "question": question,
        }
        embed = make_embed(
            title="Quick verification",
            description=f"To prove you're human, answer this:\n\n## {question}",
            colour=self.bot.config.color_main,
        )
        set_footer(embed, self.bot, f"{MAX_ATTEMPTS} attempts before a {TIMEOUT_MINUTES} minute timeout.")
        view = CaptchaView(self, nonce, owner_id, options)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def start_verification(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        config = await self.bot.db.get_config(interaction.guild.id)
        role = member.guild.get_role(config["verification_role"]) if config["verification_role"] else None

        if role is None:
            return await interaction.response.send_message(
                "Verification isn't configured on this server.", ephemeral=True
            )
        if role in getattr(member, "roles", []):
            return await interaction.response.send_message("You are already verified.", ephemeral=True)

        if config["verification_mode"] == "button":
            message = await self._grant(member, role.id)
            return await interaction.response.send_message(message, ephemeral=True)

        await self._ask_question(interaction, member.guild.id, member.id)

    async def handle_answer(self, interaction: discord.Interaction, nonce: str, chosen: int) -> None:
        entry = self.captchas.pop(nonce, None)
        if entry is None:
            return await interaction.response.send_message(
                "That question expired — press Verify again.", ephemeral=True
            )

        if entry["answer"] == chosen:
            config = await self.bot.db.get_config(entry["guild"])
            message = await self._grant(interaction.user, config["verification_role"])
            return await interaction.response.send_message(message, ephemeral=True)

        attempts = await self.bot.db.verify_bump(entry["guild"], entry["owner"])
        config = await self.bot.db.get_config(entry["guild"])

        if attempts >= MAX_ATTEMPTS:
            offender = interaction.guild.get_member(entry["owner"])
            if offender is not None:
                try:
                    await offender.timeout(
                        dt.timedelta(minutes=TIMEOUT_MINUTES),
                        reason="failed verification captcha",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
            await send_log(
                interaction.guild,
                config["log_channel"],
                action_embed(
                    "Verification failed",
                    offender or interaction.user,
                    self.bot.user,
                    f"{attempts} wrong answers",
                    colour=RED,
                ),
            )
            return await interaction.response.send_message(
                f"Too many wrong answers. You've been timed out for {TIMEOUT_MINUTES} minutes.",
                ephemeral=True,
            )

        remaining = MAX_ATTEMPTS - attempts
        response = await interaction.response.send_message(
            f"Wrong answer — **{remaining}** attempt(s) left. Here's a new one:",
            ephemeral=True,
        )
        question, answer, options = _make_question()
        new_nonce = secrets.token_hex(6)
        self.captchas[new_nonce] = {
            "answer": answer,
            "owner": entry["owner"],
            "guild": entry["guild"],
            "question": question,
        }
        embed = make_embed(description=f"## {question}", colour=RED)
        await response.edit_message(
            embed=embed, view=CaptchaView(self, new_nonce, entry["owner"], options)
        )

    # -------------------------------------------------------------- commands
    @commands.group(name="verification", aliases=["vsetup"], invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def verification(self, ctx: commands.Context) -> None:
        """Member verification / captcha system."""
        config = await self.bot.db.get_config(ctx.guild.id)
        if not config["verification_role"]:
            await ctx.send("Not configured. Run `!verification setup #channel @Role [captcha|button]`.")
            return
        role = ctx.guild.get_role(config["verification_role"])
        panel = f"<#{config['verification_channel']}>" if config["verification_channel"] else "not posted"
        role_text = role.mention if role else "missing"
        await ctx.send(
            f"Verification mode: `{config['verification_mode']}`\n"
            f"Role: {role_text}\n"
            f"Panel channel: {panel}\n"
            f"Repost it with `!verification panel`."
        )

    @verification.command(name="setup")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def verification_setup(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        role: discord.Role,
        mode: str = "captcha",
    ) -> None:
        """Enable verification. mode: captcha or button."""
        mode = mode.lower()
        if mode not in {"captcha", "button"}:
            return await ctx.send("Mode must be `captcha` or `button`.")
        if role >= ctx.me.top_role:
            return await ctx.send("That role is above my highest role — move me higher first.")

        await self.bot.db.set_config(
            ctx.guild.id,
            verification_channel=channel.id,
            verification_role=role.id,
            verification_mode=mode,
        )
        await ctx.send(
            f"Verification configured (`{mode}` mode). Now run `!verification panel` in {channel.mention}."
        )

    @verification.command(name="panel")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def verification_panel(self, ctx: commands.Context, channel: discord.TextChannel = None) -> None:
        """Post (or repost) the verification panel."""
        config = await self.bot.db.get_config(ctx.guild.id)
        if not config["verification_role"]:
            return await ctx.send("Run `!verification setup` first.")

        channel = channel or ctx.channel
        role = ctx.guild.get_role(config["verification_role"])
        is_captcha = config["verification_mode"] != "button"
        embed = make_embed(
            title="Verification",
            description=(
                "Press the button below and answer a quick maths question to prove you're human."
                if is_captcha
                else "Press the button below to verify your account."
            ),
            colour=self.bot.config.color_main,
        )
        if role:
            embed.add_field(name="You will receive", value=role.mention)
        set_footer(embed, self.bot, f"Member count: {ctx.guild.member_count}")
        message = await channel.send(embed=embed, view=VerifyView(self))
        await self.bot.db.set_config(ctx.guild.id, verification_channel=channel.id)
        await ctx.send(f"Panel posted (message id `{message.id}`).")

    @verification.command(name="grant")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def verification_grant(self, ctx: commands.Context, member: discord.Member) -> None:
        """Manually verify someone."""
        config = await self.bot.db.get_config(ctx.guild.id)
        await ctx.send(await self._grant(member, config["verification_role"], ctx.author))

    @verification.command(name="revoke")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def verification_revoke(self, ctx: commands.Context, member: discord.Member) -> None:
        """Remove the verification role from someone."""
        config = await self.bot.db.get_config(ctx.guild.id)
        role = ctx.guild.get_role(config["verification_role"]) if config["verification_role"] else None
        if role is None:
            return await ctx.send("Verification isn't configured.")
        if role not in member.roles:
            return await ctx.send(f"{member.mention} is not verified.")
        await member.remove_roles(role, reason="verification revoked")
        await self.bot.db.verify_reset(ctx.guild.id, member.id)
        await send_log(
            ctx.guild,
            config["log_channel"],
            action_embed("Verification revoked", member, ctx.author, None, colour=YELLOW),
        )
        await ctx.send(f"{member.mention} is no longer verified.")

    @verification.command(name="off")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def verification_off(self, ctx: commands.Context) -> None:
        """Disable verification."""
        await self.bot.db.set_config(
            ctx.guild.id, verification_channel=None, verification_role=None
        )
        await ctx.send("Verification disabled. Existing panels stop working.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Verification(bot))