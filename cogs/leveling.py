"""
Moduł systemów punktów doświadczenia (XP) i poziomów aktywności.
Obsługuje przyznawanie XP za wiadomości, XP za obecność w kanałach głosowych (Voice XP), generowanie graficznych kart rang w Pillow, tabelę liderów oraz nagrody w postaci ról.
"""

import time
import io
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.checks import is_administrator
from services.leveling_service import LevelingService
from utils.card_generator import create_rank_card
from utils.embeds import success_embed, error_embed, info_embed
from utils.helpers import calculate_level_from_xp, format_number

logger = logging.getLogger("AegisBot.Cogs.Leveling")

class LevelingCog(commands.Cog):
    """Zarządzanie poziomami aktywności użytkowników."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.leveling_service = LevelingService(bot.db)
        self.cooldowns: dict[tuple[int, int], float] = {}

    async def cog_load(self) -> None:
        self.voice_xp_loop.start()

    async def cog_unload(self) -> None:
        self.voice_xp_loop.cancel()

    async def _get_level_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Odczytuje kanał do ogłoszeń o awansie poziomu skonfigurowany przez
        `/config set_channel` (moduł `level_channel_id`). Zwraca None, gdy
        kanał nie został ustawiony LUB gdy bot nie może go już odnaleźć
        (np. kanał usunięty) - w obu przypadkach ogłoszenie po prostu nie
        zostaje wysłane, zamiast lądować na przypadkowym kanale."""
        row = await self.bot.db.fetchone(
            "SELECT level_channel_id FROM guild_config WHERE guild_id = ?",
            (guild.id,)
        )
        if not row or not row["level_channel_id"]:
            return None
        channel = guild.get_channel(row["level_channel_id"])
        return channel if isinstance(channel, discord.TextChannel) else None

    @tasks.loop(seconds=60)
    async def voice_xp_loop(self) -> None:
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                active_members = [m for m in vc.members if not m.bot and not m.voice.self_deaf and not m.voice.deaf]
                if len(active_members) < 2:
                    continue

                for member in active_members:
                    await self.leveling_service.add_xp(guild, member, min_xp=5, max_xp=12)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return

        key = (message.guild.id, message.author.id)
        now = time.time()
        if key in self.cooldowns and now - self.cooldowns[key] < 60:
            return

        self.cooldowns[key] = now

        leveled_up, new_level, roles_granted = await self.leveling_service.add_xp(message.guild, message.author)

        if leveled_up:
            # Ogłoszenie idzie WYŁĄCZNIE na kanał ustawiony przez administrację
            # w /config set_channel (moduł "Awanse poziomu (Level Up)") - nie na
            # kanał, na którym akurat padła wiadomość wywołująca awans. Brak
            # skonfigurowanego kanału = brak ogłoszenia (cicho, bez błędu),
            # żeby administracja mogła świadomie wyłączyć tę funkcję, po prostu
            # nie ustawiając kanału.
            announce_channel = await self._get_level_channel(message.guild)
            if announce_channel is None:
                return

            desc = f"Gratulacje {message.author.mention}! Osiągnąłeś **{new_level} poziom** aktywności! 🎉"
            if roles_granted:
                roles_str = ", ".join(r.mention for r in roles_granted)
                desc += f"\nOtrzymano nowe role: {roles_str}"
            
            embed = success_embed("Awans na Wyższy Poziom!", desc)
            try:
                await announce_channel.send(embed=embed)
            except discord.HTTPException:
                pass

    @app_commands.command(name="rank", description="Wyświetla Twoją lub wskazanego użytkownika graficzną kartę rangi.")
    @app_commands.describe(target="Użytkownik, którego rangę chcesz sprawdzić")
    async def rank(self, interaction: discord.Interaction, target: discord.Member = None) -> None:
        await interaction.response.defer()
        target = target or interaction.user

        row = await self.bot.db.fetchone(
            "SELECT xp, level FROM levels WHERE guild_id = ? AND user_id = ?",
            (interaction.guild_id, target.id)
        )

        xp = row["xp"] if row else 0
        level, needed_xp, current_xp = calculate_level_from_xp(xp)

        rank_row = await self.bot.db.fetchone(
            "SELECT COUNT(*) + 1 as rank_pos FROM levels WHERE guild_id = ? AND xp > ?",
            (interaction.guild_id, xp)
        )
        rank_pos = rank_row["rank_pos"] if rank_row else 1

        avatar_bytes = None
        try:
            avatar_bytes = await target.display_avatar.read()
        except Exception:
            pass

        card_buf = await asyncio.to_thread(
            create_rank_card,
            username=target.display_name,
            avatar_bytes=avatar_bytes,
            level=level,
            current_xp=current_xp,
            needed_xp=needed_xp,
            total_xp=xp,
            rank_pos=rank_pos
        )

        file = discord.File(card_buf, filename=f"rank-{target.id}.png")
        await interaction.followup.send(file=file)

    @app_commands.command(name="leaderboard", description="Wyświetla tabelę najaktywniejszych członków serwera.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.fetchall(
            "SELECT user_id, xp, level FROM levels WHERE guild_id = ? ORDER BY xp DESC LIMIT 10",
            (interaction.guild_id,)
        )

        if not rows:
            await interaction.response.send_message(embed=info_embed("Tabela Liderów", "Brak danych o poziomach na tym serwerze."))
            return

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, r in enumerate(rows):
            prefix = medals[idx] if idx < 3 else f"**#{idx + 1}**"
            member = interaction.guild.get_member(r["user_id"])
            name = member.mention if member else f"ID: {r['user_id']}"
            lines.append(f"{prefix} {name} — Poziom **{r['level']}** ({format_number(r['xp'])} XP)")

        embed = info_embed(f"🏆 Tabela aktywności — {interaction.guild.name}", "\n".join(lines))
        await interaction.response.send_message(embed=embed)

    xp_group = app_commands.Group(
        name="xp",
        description="Zarządzanie XP użytkowników przez administrację",
        default_permissions=discord.Permissions(administrator=True)
    )

    @xp_group.command(name="add", description="Dodaje określoną ilość XP wskazanemu użytkownikowi.")
    @app_commands.describe(target="Użytkownik", amount="Ilość XP")
    @is_administrator()
    async def xp_add(self, interaction: discord.Interaction, target: discord.Member, amount: int) -> None:
        row = await self.bot.db.fetchone("SELECT xp FROM levels WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, target.id))
        curr = row["xp"] if row else 0
        new_xp = curr + amount
        new_lvl = await self.leveling_service.set_xp(interaction.guild_id, target.id, new_xp)
        await interaction.response.send_message(embed=success_embed("Dodano XP", f"Dodano **+{amount} XP** dla {target.mention}. Nowy poziom: **{new_lvl}**."))

    @xp_group.command(name="set", description="Ustawia dokładną ilość XP wskazanemu użytkownikowi.")
    @app_commands.describe(target="Użytkownik", amount="Nowa wartość XP")
    @is_administrator()
    async def xp_set(self, interaction: discord.Interaction, target: discord.Member, amount: int) -> None:
        new_lvl = await self.leveling_service.set_xp(interaction.guild_id, target.id, amount)
        await interaction.response.send_message(embed=success_embed("Ustawiono XP", f"Ustawiono **{amount} XP** dla {target.mention}. Nowy poziom: **{new_lvl}**."))

    @app_commands.command(name="addlevelrole", description="Przypisuje nagrodę w postaci roli za osiągnięcie wskazanego poziomu.")
    @app_commands.describe(level="Wymagany poziom", role="Rola przyznawana po osiągnięciu poziomu")
    @app_commands.default_permissions(administrator=True)
    @is_administrator()
    async def addlevelrole(self, interaction: discord.Interaction, level: int, role: discord.Role) -> None:
        if level <= 0:
            await interaction.response.send_message(embed=error_embed("Błąd", "Poziom musi być większy od 0."), ephemeral=True)
            return

        await self.bot.db.execute(
            """
            INSERT INTO level_roles (guild_id, level, role_id) VALUES (?, ?, ?)
            ON CONFLICT(guild_id, level) DO UPDATE SET role_id = ?
            """,
            (interaction.guild_id, level, role.id, role.id)
        )
        await interaction.response.send_message(embed=success_embed("Dodano Nagrodę", f"Osiągnięcie **{level} poziomu** będzie od teraz nagradzane rolą {role.mention}."))

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LevelingCog(bot))