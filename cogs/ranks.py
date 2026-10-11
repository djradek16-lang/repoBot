"""
System stażu i awansów: "plusy" (punkty) prowadzą do rang typu Kapral, Sierżant, ...,
Pułkownik. Progi i role są konfigurowalne przez administrację. Po każdej zmianie liczby
punktów bot sam synchronizuje rolę członka (nadaje właściwą rangę, zdejmuje poprzednią).
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_administrator
from services.rank_service import RankService
from utils.embeds import create_embed, success_embed, error_embed, info_embed
from utils.helpers import format_number

logger = logging.getLogger("AegisBot.Cogs.Ranks")

STRIKE_LIMIT = 3
STRIKE_PAYOFF_DONATION_TYPE = "wykupienie minusa"


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _plusy_slowo(n: int) -> str:
    """Poprawna polska odmiana słowa 'plus' w zależności od liczby (1 plus, 2-4 plusy, 5+ plusów)."""
    n = abs(n)
    if n == 1:
        return "plus"
    last_two = n % 100
    last_digit = n % 10
    if 2 <= last_digit <= 4 and not (12 <= last_two <= 14):
        return "plusy"
    return "plusów"


class RanksCog(commands.Cog):
    """Staż, plusy i ranking rang organizacji."""

    STRIKE_LIMIT = STRIKE_LIMIT  # Dostępne też jako atrybut instancji - używane m.in. przez donation_views.py

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.rank_service = RankService(bot.db)

    async def cog_load(self) -> None:
        await self.rank_service._ensure_tables()

    async def _sync_rank_role(self, guild: discord.Guild, member: discord.Member, points: int) -> None:
        """Nadaje NOWĄ (wyższą) rangę, jeśli punkty na nią zasługują. Ranga NIGDY nie jest zdejmowana
        z powodu spadku punktów - "nie da się spaść z rangi", raz zdobyta zostaje na stałe."""
        ladder = await self.rank_service.get_ladder(guild.id)
        if not ladder:
            return

        threshold_by_role = {rank["role_id"]: rank["threshold"] for rank in ladder}
        held_thresholds = [threshold_by_role[role.id] for role in member.roles if role.id in threshold_by_role]
        current_held_threshold = max(held_thresholds) if held_thresholds else -1

        target = RankService.current_rank(ladder, points)
        if not target or target["threshold"] <= current_held_threshold:
            return  # Brak awansu: albo już ma tę (lub wyższą) rangę, albo punkty na nic nowego nie starczają.

        # Awans: nadajemy nową rangę i porządkujemy - zdejmujemy TYLKO niższe role z drabinki,
        # żeby nie zbierać kilku odznak naraz. To nie jest degradacja, bo dotyczy wyłącznie ról
        # niższych niż nowo zdobyta; nic nie jest zdejmowane z powodu utraty punktów.
        roles_to_remove = [role for role in member.roles if role.id in threshold_by_role and role.id != target["role_id"]]
        if roles_to_remove:
            try:
                await member.remove_roles(*roles_to_remove, reason="Awans - porządkowanie niższych rang")
            except discord.HTTPException as e:
                logger.warning(f"Nie udało się uporządkować niższych rang członka {member.id} na serwerze {guild.id}: {e}")

        target_role = guild.get_role(target["role_id"])
        if target_role and target_role not in member.roles:
            try:
                await member.add_roles(target_role, reason="Awans (system stażu)")
            except discord.HTTPException as e:
                logger.warning(f"Nie udało się nadać rangi {target['rank_name']} członkowi {member.id}: {e}")

    async def _add_strike_and_maybe_kick(
        self, interaction: discord.Interaction, member: discord.Member, reason: str
    ) -> tuple[int, bool]:
        """Dodaje minusa; jeśli licznik osiągnie STRIKE_LIMIT, automatycznie wyrzuca z serwera
        (po nieudanej próbie powiadomienia na DM) i loguje to na kanale logów (jeśli skonfigurowany).
        Zwraca (nowa_liczba_minusów, czy_wyrzucono)."""
        await self.rank_service.adjust_strikes(interaction.guild_id, member.id, 1, interaction.user.id, reason, remove=False)
        new_count = await self.rank_service.get_strike_count(interaction.guild_id, member.id)

        if new_count < STRIKE_LIMIT:
            return new_count, False

        try:
            await member.send(
                embed=error_embed(
                    "Wyrzucenie z Organizacji",
                    f"Otrzymałeś **{STRIKE_LIMIT}. minus** i zostałeś automatycznie usunięty z organizacji na serwerze **{interaction.guild.name}**.\nPowód ostatniego minusa: {reason}"
                )
            )
        except discord.HTTPException:
            pass  # Zablokowane DM-y u członka nie blokują wyrzucenia.

        kicked = False
        try:
            await member.kick(reason=f"Osiągnięto {STRIKE_LIMIT} minusy (system stażu) - ostatni powód: {reason}")
            kicked = True
        except discord.HTTPException as e:
            logger.warning(f"Nie udało się automatycznie wyrzucić {member.id} po {STRIKE_LIMIT} minusach: {e}")

        logs_cog = self.bot.get_cog("LogsCog")
        if logs_cog:
            try:
                log_channel = await logs_cog.get_log_channel(interaction.guild_id)
                if log_channel:
                    status_line = "Wyrzucony z serwera." if kicked else "⚠️ Nie udało się automatycznie wyrzucić (sprawdź uprawnienia bota)."
                    await log_channel.send(
                        embed=error_embed(
                            "🚫 Automatyczne Wyrzucenie - 3 Minusy",
                            f"**Członek:** {member.mention} ({member.id})\n**Powód ostatniego minusa:** {reason}\n**Nadał:** {interaction.user.mention}\n\n{status_line}"
                        )
                    )
            except Exception as e:
                logger.warning(f"Nie udało się zalogować automatycznego wyrzucenia na kanale logów: {e}")

        return new_count, kicked

    async def payoff_strike(self, guild: discord.Guild, member: discord.Member) -> int:
        """Zdejmuje jednego minusa w ramach wykupienia (wywoływane z DonationReviewView po
        zatwierdzeniu wpłaty typu 'wykupienie minusa'). Zwraca nową liczbę minusów."""
        await self.rank_service.adjust_strikes(guild.id, member.id, 1, admin_id=None, reason="Wykupienie minusa (zatwierdzona wpłata)", remove=True)
        return await self.rank_service.get_strike_count(guild.id, member.id)

    staz_group = app_commands.Group(name="staz", description="Staż, plusy i ranking rang organizacji")

    staz_admin_group = app_commands.Group(
        name="stazadmin",
        description="Zarządzanie systemem stażu i rang (tylko administracja)",
        default_permissions=discord.Permissions(administrator=True)
    )

    @staz_group.command(name="moje", description="Pokazuje profil stażu: rangę, plusy, czas na kanałach głosowych i wiadomości.")
    @app_commands.describe(uzytkownik="Czyj profil sprawdzić (domyślnie Twój własny)")
    async def staz_moje(self, interaction: discord.Interaction, uzytkownik: discord.Member | None = None) -> None:
        member = uzytkownik or interaction.user
        guild_id = interaction.guild_id

        points = await self.rank_service.get_points(guild_id, member.id)
        ladder = await self.rank_service.get_ladder(guild_id)
        current = RankService.current_rank(ladder, points)
        following = RankService.next_rank(ladder, points)

        voice_seconds = await self.rank_service.get_alltime_voice_seconds(guild_id, member.id)
        messages = await self.rank_service.get_alltime_messages(guild_id, member.id)
        strikes = await self.rank_service.get_strike_count(guild_id, member.id)
        days_in_org = (discord.utils.utcnow() - member.joined_at).days if member.joined_at else 0

        rank_text = current["rank_name"] if current else "Saper"
        if following:
            missing = following["threshold"] - points
            next_text = f"**{following['rank_name']}** (brakuje **+{missing}**)"
        else:
            next_text = "🏆 Najwyższa ranga osiągnięta!" if ladder else "Drabinka rang nie jest jeszcze skonfigurowana."

        embed = create_embed(
            title=f"📋 Profil Stażu - {member.display_name}",
            color=discord.Color.blurple(),
            fields=[
                {"name": "Staż", "value": f"**{days_in_org}** dni", "inline": True},
                {"name": "Ranga", "value": f"**{rank_text}**", "inline": True},
                {"name": "Plusy", "value": f"**+{points}**", "inline": True},
                {"name": "Następna ranga", "value": next_text, "inline": False},
                {"name": "Czas na kanałach głosowych (all-time)", "value": _format_duration(voice_seconds), "inline": True},
                {"name": "Wysłane wiadomości (all-time)", "value": format_number(messages), "inline": True},
                {"name": "Minusy", "value": f"**{strikes}/{STRIKE_LIMIT}**" + (" ⚠️" if strikes > 0 else ""), "inline": True},
            ]
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @staz_group.command(name="ranking", description="Ranking członków po liczbie zdobytych plusów.")
    async def staz_ranking(self, interaction: discord.Interaction) -> None:
        leaderboard = await self.rank_service.get_leaderboard(interaction.guild_id, limit=10)
        if not leaderboard:
            await interaction.response.send_message(
                embed=info_embed("🏆 Ranking Stażu", "Nikt jeszcze nie ma żadnych plusów.")
            )
            return

        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines = [
            f"{medals.get(i, f'`#{i + 1}`')} <@{entry['user_id']}> — **+{entry['total']}** {_plusy_slowo(entry['total'])}"
            for i, entry in enumerate(leaderboard)
        ]
        embed = create_embed(title="🏆 Ranking Stażu", description="\n".join(lines), color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)

    @staz_admin_group.command(name="ustaw_range", description="Definiuje jeden stopień drabinki rang (nazwa, próg w plusach, rola).")
    @app_commands.describe(
        nazwa="Nazwa rangi (np. Kapral, Sierżant, Pułkownik)",
        prog="Ile plusów potrzeba, żeby ją osiągnąć",
        rola="Rola na Discordzie odpowiadająca tej randze"
    )
    @is_administrator()
    async def staz_ustaw_range(self, interaction: discord.Interaction, nazwa: str, prog: app_commands.Range[int, 1], rola: discord.Role) -> None:
        await self.rank_service.set_rank(interaction.guild_id, nazwa, prog, rola.id)
        await interaction.response.send_message(
            embed=success_embed("Zaktualizowano Drabinkę Rang", f"Ranga **{nazwa}** wymaga teraz **+{prog}** i odpowiada roli {rola.mention}.")
        )

    @staz_admin_group.command(name="lista_rang", description="Pokazuje aktualnie skonfigurowaną drabinkę rang.")
    @is_administrator()
    async def staz_lista_rang(self, interaction: discord.Interaction) -> None:
        ladder = await self.rank_service.get_ladder(interaction.guild_id)
        if not ladder:
            await interaction.response.send_message(
                embed=info_embed("Drabinka Rang", "Nie skonfigurowano jeszcze żadnej rangi. Użyj `/stazadmin ustaw_range`."),
                ephemeral=True
            )
            return

        lines = [f"**{rank['rank_name']}** — +{rank['threshold']} — <@&{rank['role_id']}>" for rank in ladder]
        await interaction.response.send_message(
            embed=create_embed(title="📜 Drabinka Rang", description="\n".join(lines), color=discord.Color.blurple()),
            ephemeral=True
        )

    @staz_admin_group.command(name="dodaj", description="Dodaje plusy użytkownikowi - automatycznie synchronizuje rangę.")
    @app_commands.describe(uzytkownik="Komu dodać plusy", ilosc="Ile plusów dodać", powod="Powód (np. za co przyznane)")
    @is_administrator()
    async def staz_dodaj(self, interaction: discord.Interaction, uzytkownik: discord.Member, ilosc: app_commands.Range[int, 1], powod: str) -> None:
        await self.rank_service.adjust_points(interaction.guild_id, uzytkownik.id, ilosc, interaction.user.id, powod, remove=False)
        new_total = await self.rank_service.get_points(interaction.guild_id, uzytkownik.id)
        await self._sync_rank_role(interaction.guild, uzytkownik, new_total)

        await interaction.response.send_message(
            embed=success_embed(
                "Dodano Plusy",
                f"Dodano **+{ilosc}** użytkownikowi {uzytkownik.mention}.\nPowód: {powod}\nNowa suma: **+{new_total}**"
            )
        )

    @staz_admin_group.command(name="usun", description="Odejmuje plusy użytkownikowi - automatycznie synchronizuje rangę.")
    @app_commands.describe(uzytkownik="Komu odjąć plusy", ilosc="Ile plusów odjąć", powod="Powód (np. korekta pomyłki)")
    @is_administrator()
    async def staz_usun(self, interaction: discord.Interaction, uzytkownik: discord.Member, ilosc: app_commands.Range[int, 1], powod: str) -> None:
        await self.rank_service.adjust_points(interaction.guild_id, uzytkownik.id, ilosc, interaction.user.id, powod, remove=True)
        new_total = await self.rank_service.get_points(interaction.guild_id, uzytkownik.id)
        await self._sync_rank_role(interaction.guild, uzytkownik, new_total)

        await interaction.response.send_message(
            embed=success_embed(
                "Odjęto Plusy",
                f"Odjęto **+{ilosc}** użytkownikowi {uzytkownik.mention}.\nPowód: {powod}\nNowa suma: **+{new_total}**"
            )
        )

    @staz_admin_group.command(name="dodaj_minus", description=f"Nadaje minusa - przy {STRIKE_LIMIT} minusach członek jest automatycznie wyrzucany z serwera.")
    @app_commands.describe(uzytkownik="Komu nadać minusa", powod="Powód nadania minusa")
    @is_administrator()
    async def staz_dodaj_minus(self, interaction: discord.Interaction, uzytkownik: discord.Member, powod: str) -> None:
        await interaction.response.defer()
        new_count, kicked = await self._add_strike_and_maybe_kick(interaction, uzytkownik, powod)

        if kicked:
            await interaction.followup.send(
                embed=error_embed(
                    "🚫 Wyrzucono z Organizacji",
                    f"{uzytkownik.mention} otrzymał **{STRIKE_LIMIT}. minusa** i został automatycznie wyrzucony z serwera.\nPowód ostatniego minusa: {powod}"
                )
            )
            return

        remaining = STRIKE_LIMIT - new_count
        await interaction.followup.send(
            embed=success_embed(
                "Dodano Minusa",
                f"{uzytkownik.mention} ma teraz **{new_count}/{STRIKE_LIMIT}** minusy.\nPowód: {powod}\n"
                f"Jeszcze **{remaining}** i zostanie automatycznie wyrzucony z organizacji "
                f"(minusa można wykupić za `/wplata zgloszenie` typu **{STRIKE_PAYOFF_DONATION_TYPE}**)."
            )
        )

    @staz_admin_group.command(name="usun_minus", description="Ręcznie zdejmuje minusa użytkownikowi (korekta - bez opłaty).")
    @app_commands.describe(uzytkownik="Komu zdjąć minusa", powod="Powód zdjęcia minusa")
    @is_administrator()
    async def staz_usun_minus(self, interaction: discord.Interaction, uzytkownik: discord.Member, powod: str) -> None:
        await self.rank_service.adjust_strikes(interaction.guild_id, uzytkownik.id, 1, interaction.user.id, powod, remove=True)
        new_count = await self.rank_service.get_strike_count(interaction.guild_id, uzytkownik.id)
        await interaction.response.send_message(
            embed=success_embed("Zdjęto Minusa", f"{uzytkownik.mention} ma teraz **{new_count}/{STRIKE_LIMIT}** minusy.\nPowód: {powod}")
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RanksCog(bot))