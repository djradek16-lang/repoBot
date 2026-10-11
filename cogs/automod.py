"""
Moduł automatycznej moderacji (AutoMod).
Filtruje zaproszenia do Discorda, zabronione linki, masowe wzmianki, nadmierny CAPS LOCK, spam oraz zakazane słowa.
"""

import re
import json
import time
import logging
import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from services.punishment_service import PunishmentService
from core.constants import ModerationAction
from utils.embeds import warning_embed, success_embed, error_embed, info_embed

logger = logging.getLogger("AegisBot.Cogs.AutoMod")

INVITE_REGEX = re.compile(r"(discord\.(gg|io|me|li)|discordapp\.com\/invite|discord\.com\/invite)\/([a-zA-Z0-9\-]+)", re.IGNORECASE)
LINK_REGEX = re.compile(r"https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)", re.IGNORECASE)

class AutoModCog(commands.Cog):
    """System automatycznej ochrony przed spamem i niechcianymi treściami."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.punishment_service = PunishmentService(bot.db)
        self.user_message_times: dict[tuple[int, int], list[float]] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return

        if message.author.guild_permissions.manage_messages or message.author.guild_permissions.administrator:
            return

        guild = message.guild
        rule = await self.bot.db.fetchone("SELECT * FROM automod_rules WHERE guild_id = ?", (guild.id,))
        if not rule:
            await self.bot.db.execute("INSERT OR IGNORE INTO automod_rules (guild_id) VALUES (?)", (guild.id,))
            rule = await self.bot.db.fetchone("SELECT * FROM automod_rules WHERE guild_id = ?", (guild.id,))

        if not rule:
            return

        try:
            ignored_channels = json.loads(rule["ignored_channels"] or "[]")
            ignored_roles = json.loads(rule["ignored_roles"] or "[]")
        except Exception:
            ignored_channels, ignored_roles = [], []

        if message.channel.id in ignored_channels:
            return

        for r in message.author.roles:
            if r.id in ignored_roles:
                return

        content = message.content or ""
        reason = None

        if rule["block_invites"] and INVITE_REGEX.search(content):
            reason = "Automatyczny filtr: Wysyłanie zaproszeń Discord"

        elif rule["block_links"] and LINK_REGEX.search(content):
            try:
                allowed = json.loads(rule["allowed_links"] or "[]")
            except Exception:
                allowed = []
            
            is_allowed = any(domain in content for domain in allowed)
            if not is_allowed:
                reason = "Automatyczny filtr: Publikowanie zewnętrznych linków"

        elif rule["max_mentions"] and len(message.mentions) > rule["max_mentions"]:
            reason = f"Automatyczny filtr: Przekroczono limit wzmianek ({len(message.mentions)}/{rule['max_mentions']})"

        elif rule["caps_limit"] and len(content) > 10:
            uppercase_chars = sum(1 for c in content if c.isupper())
            caps_percent = int((uppercase_chars / len(content)) * 100)
            if caps_percent > rule["caps_limit"]:
                reason = f"Automatyczny filtr: Nadmierny CAPS LOCK ({caps_percent}% > {rule['caps_limit']}%)"

        elif rule["forbidden_words"]:
            try:
                bad_words = json.loads(rule["forbidden_words"] or "[]")
            except Exception:
                bad_words = []
            
            content_lower = content.lower()
            for word in bad_words:
                if word.lower() in content_lower:
                    reason = f"Automatyczny filtr: Użycie niedozwolonego słowa"
                    break

        if not reason and rule["antispam_enabled"]:
            key = (guild.id, message.author.id)
            now = time.time()
            if key not in self.user_message_times:
                self.user_message_times[key] = []
            
            self.user_message_times[key].append(now)
            self.user_message_times[key] = [t for t in self.user_message_times[key] if now - t < 4.0]

            if len(self.user_message_times[key]) >= 5:
                reason = "Automatyczny filtr: Wykryto szybki spam wiadomościami"

        if reason:
            try:
                await message.delete()
            except discord.HTTPException:
                pass

            action = rule["action"] or "delete"

            try:
                warning_emb = warning_embed("Ostrzeżenie AutoMod", f"Wiadomość użytkownika {message.author.mention} została usunięta.\n**Powód:** {reason}")
                await message.channel.send(embed=warning_emb, delete_after=6.0)
            except discord.HTTPException:
                pass

            if action in ["warn", "timeout"]:
                await self.punishment_service.apply_punishment(
                    guild=guild,
                    moderator=guild.me,
                    target=message.author,
                    action=ModerationAction.WARN if action == "warn" else ModerationAction.TIMEOUT,
                    reason=reason,
                    duration_seconds=600 if action == "timeout" else None
                )

    automod_group = app_commands.Group(
        name="automod",
        description="Konfiguracja modułu AutoMod",
        default_permissions=discord.Permissions(administrator=True)
    )

    @automod_group.command(name="status", description="Wyświetla bieżący status i zasady AutoModa.")
    @is_administrator()
    async def automod_status(self, interaction: discord.Interaction) -> None:
        rule = await self.bot.db.fetchone("SELECT * FROM automod_rules WHERE guild_id = ?", (interaction.guild_id,))
        if not rule:
            await self.bot.db.execute("INSERT INTO automod_rules (guild_id) VALUES (?)", (interaction.guild_id,))
            rule = await self.bot.db.fetchone("SELECT * FROM automod_rules WHERE guild_id = ?", (interaction.guild_id,))

        embed = info_embed(f"🛡️ Status AutoMod — {interaction.guild.name}", "Bieżące ustawienia filtrów automatycznych:")
        
        info = (
            f"**Anti-Spam:** {'✅ Włączony' if rule['antispam_enabled'] else '❌ Wyłączony'}\n"
            f"**Blokada Zaproszeń:** {'✅ Włączona' if rule['block_invites'] else '❌ Wyłączona'}\n"
            f"**Blokada Linków:** {'✅ Włączona' if rule['block_links'] else '❌ Wyłączona'}\n"
            f"**Limit Wzmianek:** `{rule['max_mentions']}` osób\n"
            f"**Limit CapsLock:** `{rule['caps_limit']}%`\n"
            f"**Reakcja bota:** `{rule['action']}`"
        )
        embed.add_field(name="Zasady Ochrony", value=info, inline=False)
        await interaction.response.send_message(embed=embed)

    @automod_group.command(name="set_rule", description="Zmienia parametry wybranej zasady AutoMod.")
    @app_commands.describe(rule="Wybierz regułę", enabled="Włącz (True) / Wyłącz (False)")
    @app_commands.choices(rule=[
        app_commands.Choice(name="Anti-Spam", value="antispam_enabled"),
        app_commands.Choice(name="Blokada Zaproszeń Discord", value="block_invites"),
        app_commands.Choice(name="Blokada Zewnętrznych Linków", value="block_links"),
    ])
    @is_administrator()
    async def set_rule(self, interaction: discord.Interaction, rule: str, enabled: bool) -> None:
        val = 1 if enabled else 0
        await self.bot.db.execute(
            f"INSERT INTO automod_rules (guild_id, {rule}) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET {rule} = ?",
            (interaction.guild_id, val, val)
        )
        status_txt = "włączona" if enabled else "wyłączona"
        await interaction.response.send_message(embed=success_embed("Zaktualizowano AutoMod", f"Reguła `{rule}` została **{status_txt}**."))

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoModCog(bot))
