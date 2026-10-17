"""
Moduł komend narzędziowych i informacyjnych (Utility).
Zawiera komendy informacyjne o użytkownikach, serwerze, bocie, ankietach, kalkulatorze oraz interaktywny /help z Select Menu.
"""

import sys
import platform
import logging
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.embeds import success_embed, error_embed, info_embed, create_embed
from utils.time_parser import parse_time, format_time
import config

logger = logging.getLogger("AegisBot.Cogs.Utility")

def _check_is_admin(user: discord.User | discord.Member, guild: discord.Guild | None) -> bool:
    """Sprawdza czy użytkownik ma uprawnienia administratora na serwerze lub jest właścicielem bota."""
    if user.id == config.OWNER_ID:
        return True
    if isinstance(user, discord.Member):
        if user.guild_permissions.administrator:
            return True
        if guild and user.id == guild.owner_id:
            return True
    return False

class HelpSelect(discord.ui.Select):
    """Kategorie komend bota w rozwijanym menu /help z uwzględnieniem uprawnień użytkownika."""

    def __init__(self, is_admin: bool = False, is_owner: bool = False) -> None:
        self.is_admin = is_admin
        self.is_owner = is_owner

        options = [
            discord.SelectOption(label="🎮 MTA: ProjectRPG", value="prpg", description="Status serwera, zarobki, tuning, SAPD, wzory apelacji"),
            discord.SelectOption(label="📻 Audycje & Radio Project FM", value="audycja", description="Ramówka audycji, powiadomienia i Radio"),
            discord.SelectOption(label="🧰 Narzędzia & Pomoc", value="utility", description="Informacje, przypomnienia, ankiety"),
        ]

        if self.is_admin:
            options.insert(0, discord.SelectOption(label="🛡️ Moderacja & AutoMod", value="mod", description="Kary, wyciszenia, filtry spamu"))
            options.insert(1, discord.SelectOption(label="⚙️ Administracja", value="admin", description="Konfiguracja kanałów, ról i opcji"))
            options.append(discord.SelectOption(label="🎉 Konkursy (Giveaways)", value="giveaways", description="Zarządzanie konkursami"))

        if self.is_owner:
            options.append(discord.SelectOption(label="👑 Właściciel Bota", value="owner", description="Restart, zarządzenie modułami"))

        super().__init__(placeholder="Wybierz kategorię komend...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        cat = self.values[0]
        
        # Słownik opisu komend dla graczy i adminów
        embeds_data = {
            "mod": ("🛡️ Moderacja & AutoMod", "` /ban `, ` /softban `, ` /tempban `, ` /unban `, ` /kick `, ` /mute `, ` /unmute `, ` /warn `, ` /delwarn `, ` /warnings `, ` /clearwarns `, ` /clear `, ` /slowmode `, ` /lock `, ` /unlock `, ` /automod status `, ` /automod set_rule `"),
            "admin": ("⚙️ Administracja Serwera", "` /config show `, ` /config set_channel `, ` /config set_role `, ` /config set_toggle `, ` /config set_message `"),
            "tickets": ("🎫 Zgłoszenia (Ticket System)", "` Panel weryfikacyjny (wybierz kategorię w wiadomości) `, ` /ticket add `, ` /ticket remove `" + (" , ` /ticket setup `, ` /ticket rename `" if self.is_admin else "")),
            "levels": ("⭐ Poziomy XP & Aktywność", "` /rank `, ` /leaderboard `" + (" , ` /xp add `, ` /xp set `, ` /addlevelrole `" if self.is_admin else "")),
            "economy": ("💰 Ekonomia & Sklep", "` /balance `, ` /deposit `, ` /withdraw `, ` /daily `, ` /work `, ` /crime `, ` /rob `, ` /pay `, ` /shop `, ` /buy `, ` /inventory `, ` /slots `" + (" , ` /shopadmin add `, ` /shopadmin remove `, ` /eco add `, ` /eco remove `" if self.is_admin else "")),
            "giveaways": ("🎉 Konkursy (Giveaways)", "` Kliknij przycisk na kanale z konkursem `" + (" , ` /giveaway start `, ` /giveaway end `, ` /giveaway reroll `" if self.is_admin else "")),
            "suggestions": ("💡 Sugestie i Propozycje", "` /suggest `" + (" , ` /suggestion accept `, ` /suggestion reject `" if self.is_admin else "")),
            "fun": ("🎲 Rozrywka i Hazard", "` /8ball `, ` /coinflip `, ` /roll `, ` /meme `, ` /cat `, ` /dog `, ` /hug `, ` /slap `"),
            "prpg": ("🎮 MTA: ProjectRPG", "` /prpg status `, ` /prpg gracze `, ` /prpg szukaj `, ` /prpg zarobki `, ` /prpg tuning `, ` /prpg taryfikator `, ` /prpg apelacja `, ` /prpg frakcje `"),
            "audycja": ("📻 Audycje & Radio Project FM", "` /audycja `, ` /radio `, ` /projectfm `"),
            "utility": ("🧰 Narzędzia i Pomoc", "` /help `, ` /ping `, ` /userinfo `, ` /serverinfo `, ` /avatar `, ` /botinfo `, ` /remind `, ` /poll `, ` /math `"),
            "owner": ("👑 Komendy Właściciela", "` /owner reload `, ` /owner sync `, ` /owner servers `, ` /owner eval `, ` /owner restart `, ` /owner shutdown `, ` /owner blacklist `")
        }

        title, desc = embeds_data.get(cat, ("Kategoria", "Brak opisu"))
        emb = info_embed(title, f"Lista dostępnych poleceń w tej kategorii:\n\n{desc}")
        await interaction.response.edit_message(embed=emb, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, is_admin: bool = False, is_owner: bool = False) -> None:
        super().__init__(timeout=120)
        self.add_item(HelpSelect(is_admin=is_admin, is_owner=is_owner))


class UtilityCog(commands.Cog):
    """Komendy informacyjne i pomocnicze."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.check_reminders.start()

    async def cog_unload(self) -> None:
        self.check_reminders.cancel()

    @tasks.loop(seconds=15)
    async def check_reminders(self) -> None:
        now = datetime.now()
        rows = await self.bot.db.fetchall("SELECT reminder_id, user_id, channel_id, text FROM reminders WHERE trigger_at <= ?", (now.isoformat(),))
        for r in rows:
            await self.bot.db.execute("DELETE FROM reminders WHERE reminder_id = ?", (r["reminder_id"],))
            channel = self.bot.get_channel(r["channel_id"])
            if channel and isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(f"⏰ <@{r['user_id']}> **Przypomnienie:** {r['text']}")
                except discord.HTTPException:
                    pass

    @app_commands.command(name="ping", description="Sprawdza opóźnienie (ping) bota do API Discorda.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(embed=success_embed("Pong! 🏓", f"Opóźnienie bota wynosi **{latency} ms**."))

    @app_commands.command(name="userinfo", description="Wyświetla szczegółowe informacje o użytkowniku.")
    @app_commands.describe(target="Wybrany użytkownik")
    async def userinfo(self, interaction: discord.Interaction, target: discord.Member = None) -> None:
        target = target or interaction.user
        roles = [r.mention for r in target.roles[1:]] or ["Brak ról"]

        created_at = target.created_at.strftime("%Y-%m-%d %H:%M:%S")
        joined_at = target.joined_at.strftime("%Y-%m-%d %H:%M:%S") if target.joined_at else "Brak danych"

        embed = info_embed(f"👤 Informacje o Użytkowniku — {target.display_name}", f"ID: `{target.id}`")
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Account Name", value=str(target), inline=True)
        embed.add_field(name="Bot?", value="Tak" if target.bot else "Nie", inline=True)
        embed.add_field(name="Data Utworzenia", value=created_at, inline=False)
        embed.add_field(name="Data Dołączenia", value=joined_at, inline=False)
        embed.add_field(name=f"Role [{len(target.roles)-1}]", value=", ".join(roles[:10]), inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="Wyświetla szczegółowe informacje o tym serwerze.")
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        embed = info_embed(f"🏰 Informacje o Serwerze — {guild.name}", f"ID: `{guild.id}`")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        owner = guild.owner.mention if guild.owner else f"ID: {guild.owner_id}"
        embed.add_field(name="Właściciel", value=owner, inline=True)
        embed.add_field(name="Liczba Członków", value=str(guild.member_count), inline=True)
        embed.add_field(name="Liczba Kanałów", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Liczba Ról", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Poziom Boosta", value=f"Poziom {guild.premium_tier} ({guild.premium_subscription_count} boostów)", inline=False)
        embed.add_field(name="Data Utworzenia", value=guild.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="Wyświetla awatar wskazanego użytkownika w pełnym rozmiarze.")
    @app_commands.describe(target="Wybrany użytkownik")
    async def avatar(self, interaction: discord.Interaction, target: discord.User = None) -> None:
        target = target or interaction.user
        embed = info_embed(f"🖼️ Awatar użytkownika {target.display_name}", f"[Pobierz Awatar]({target.display_avatar.url})")
        embed.set_image(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="botinfo", description="Wyświetla specyfikację i stan bota AegisBot.")
    async def botinfo(self, interaction: discord.Interaction) -> None:
        embed = info_embed("🤖 AegisBot — Status i Informacje", "Zaawansowany bot Discord stworzony w Pythonie 3.12 (discord.py 2.x)")
        embed.add_field(name="Wersja Pythona", value=platform.python_version(), inline=True)
        embed.add_field(name="Wersja discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Liczba Serwerów", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Liczba Komend", value=str(len(self.bot.tree.get_commands())), inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remind", description="Ustawia przypomnienie tekstowe na określony czas.")
    @app_commands.describe(duration="Czas za ile przypomnieć (np. 10m, 2h, 1d)", text="Treść przypomnienia")
    async def remind(self, interaction: discord.Interaction, duration: str, text: str) -> None:
        seconds = parse_time(duration)
        if not seconds or seconds < 10:
            await interaction.response.send_message(embed=error_embed("Błąd", "Podaj prawidłowy czas przypomnienia (minimum 10 sekund)."), ephemeral=True)
            return

        trigger_at = datetime.now() + timedelta(seconds=seconds)

        await self.bot.db.execute(
            "INSERT INTO reminders (user_id, channel_id, text, trigger_at) VALUES (?, ?, ?, ?)",
            (interaction.user.id, interaction.channel_id, text, trigger_at.isoformat())
        )

        await interaction.response.send_message(embed=success_embed("Ustawiono Przypomnienie", f"Przypomnę Ci za **{format_time(seconds)}**: *{text}*"))

    @app_commands.command(name="poll", description="Tworzy szybką ankietę do głosowania.")
    @app_commands.describe(question="Pytanie ankiety")
    async def poll(self, interaction: discord.Interaction, question: str) -> None:
        embed = info_embed("📊 Ankieta Społeczności", f"**Pytanie:** {question}\n\nGłosuj poniżej za pomocą reakcji!")
        embed.set_footer(text=f"Utworzono przez {interaction.user}")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")

    @app_commands.command(name="math", description="Bezpieczny kalkulator matematyczny.")
    @app_commands.describe(expression="Wyrażenie (np. 2 + 2 * 5)")
    async def math_calc(self, interaction: discord.Interaction, expression: str) -> None:
        allowed = set("0123456789+-*/(). ")
        if not set(expression).issubset(allowed):
            await interaction.response.send_message(embed=error_embed("Błąd", "Wyrażenie zawiera niedozwolone znaki."), ephemeral=True)
            return

        try:
            result = eval(expression, {"__builtins__": None}, {})
            await interaction.response.send_message(embed=success_embed("Kalkulator 🧮", f"**Wyrażenie:** `{expression}`\n**Wynik:** `{result}`"))
        except Exception as e:
            await interaction.response.send_message(embed=error_embed("Błąd Obliczeń", f"Nie udało się obliczyć wyrażenia: {e}"), ephemeral=True)

    @app_commands.command(name="help", description="Wyświetla interaktywne centrum pomocy bota.")
    async def help_command(self, interaction: discord.Interaction) -> None:
        is_admin = _check_is_admin(interaction.user, interaction.guild)
        is_owner = interaction.user.id == config.OWNER_ID

        embed = info_embed("📚 Centrum Pomocy — AegisBot", "Wybierz kategorię z poniższego menu rozwijanego, aby zobaczyć listę dostępnych komend.")
        embed.set_thumbnail(url=self.bot.user.display_avatar.url if self.bot.user else None)
        await interaction.response.send_message(embed=embed, view=HelpView(is_admin=is_admin, is_owner=is_owner))

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilityCog(bot))