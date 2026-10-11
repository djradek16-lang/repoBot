"""
Liczniki na kanałach głosowych: "Członkowie: xx" i "Ostatni kto przybył: Nick".
Kanały głosowe służą tu wyłącznie jako "wyświetlacz" (bot zmienia ich nazwę) -
nikt nie ma do nich realnie dołączać. Nazwę kanału Discord pozwala zmieniać
maksymalnie 2 razy na 10 minut, dlatego licznik członków jest odświeżany
cyklicznie (co 10 minut), a "ostatni dołączony" aktualizuje się od razu przy
dołączeniu (z uwzględnieniem tego samego limitu - patrz komentarze niżej).
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.checks import is_administrator
from utils.embeds import success_embed, error_embed

logger = logging.getLogger("AegisBot.Cogs.MemberCounter")

MEMBER_COUNT_PREFIX = "📊 Członkowie"
LAST_JOINED_PREFIX = "🆕 Ostatni"


class MemberCounterCog(commands.Cog):
    """Liczniki członków i ostatnio dołączonej osoby na kanałach głosowych."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.refresh_member_counts.start()

    async def cog_unload(self) -> None:
        self.refresh_member_counts.cancel()

    async def _get_config(self, guild_id: int) -> dict | None:
        row = await self.bot.db.fetchone(
            "SELECT member_count_channel_id, last_joined_channel_id, member_count_role_id FROM guild_config WHERE guild_id = ?",
            (guild_id,)
        )
        return dict(row) if row else None

    async def _update_member_count_channel(self, guild: discord.Guild, channel_id: int) -> None:
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return

        config = await self._get_config(guild.id)
        role_id = config["member_count_role_id"] if config else None

        if role_id:
            role = guild.get_role(role_id)
            count = len(role.members) if role else 0
        else:
            count = guild.member_count

        new_name = f"{MEMBER_COUNT_PREFIX}: {count}"
        if channel.name == new_name:
            return  # Bez zmian - nie marnujemy limitu edycji nazwy na identyczną wartość.

        try:
            await channel.edit(name=new_name)
        except discord.HTTPException as e:
            logger.warning(f"Nie udało się zaktualizować kanału-licznika członków na serwerze {guild.id}: {e}")

    async def _update_last_joined_channel(self, guild: discord.Guild, channel_id: int, member: discord.Member) -> None:
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return

        display_name = member.display_name[:80]  # Zapas na prefiks + limit 100 znaków dla nazwy kanału.
        new_name = f"{LAST_JOINED_PREFIX}: {display_name}"

        try:
            await channel.edit(name=new_name)
        except discord.HTTPException as e:
            # Najczęstsza przyczyna: limit Discorda (2 zmiany nazwy / 10 min na kanał) przy kilku
            # dołączeniach pod rząd - biblioteka sama odczeka swoje, ale gdyby mimo to się nie
            # udało, po prostu logujemy i nie aktualizujemy tym razem (kolejne dołączenie i tak nadpisze).
            logger.warning(f"Nie udało się zaktualizować kanału 'ostatni dołączony' na serwerze {guild.id}: {e}")

    @commands.Cog.listener("on_member_join")
    async def on_member_join(self, member: discord.Member) -> None:
        config = await self._get_config(member.guild.id)
        if not config:
            return

        if config["member_count_channel_id"]:
            await self._update_member_count_channel(member.guild, config["member_count_channel_id"])

    @commands.Cog.listener("on_member_remove")
    async def on_member_remove(self, member: discord.Member) -> None:
        config = await self._get_config(member.guild.id)
        if config and config["member_count_channel_id"]:
            await self._update_member_count_channel(member.guild, config["member_count_channel_id"])

    @commands.Cog.listener("on_member_update")
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Licznik liczy osoby z konkretną rolą, więc musi się też odświeżać, gdy ktoś tę
        rolę dostanie albo straci (nie tylko przy dołączeniu/opuszczeniu serwera). Kanał
        'ostatni' aktualizuje się wyłącznie przy NADANIU roli - nie przy dołączeniu do serwera."""
        if before.roles == after.roles:
            return

        config = await self._get_config(after.guild.id)
        if not config or not config["member_count_role_id"]:
            return

        role_id = config["member_count_role_id"]
        had_role = any(r.id == role_id for r in before.roles)
        has_role = any(r.id == role_id for r in after.roles)
        if had_role != has_role:
            if config["member_count_channel_id"]:
                await self._update_member_count_channel(after.guild, config["member_count_channel_id"])

            # "Ostatni" ma pokazywać osobę, która DOSTAŁA rolę - nie tę, która ją straciła.
            if has_role and config["last_joined_channel_id"]:
                await self._update_last_joined_channel(after.guild, config["last_joined_channel_id"], after)

    @tasks.loop(minutes=10)
    async def refresh_member_counts(self) -> None:
        """Odświeża liczniki członków na wszystkich serwerach co 10 minut - zgodnie z limitem
        Discorda na zmianę nazwy kanału. To jest główne, niezawodne źródło aktualizacji (na wypadek,
        gdyby jakiś on_member_join/remove został pominięty, np. przez chwilowy brak połączenia)."""
        for guild in self.bot.guilds:
            try:
                config = await self._get_config(guild.id)
                if config and config["member_count_channel_id"]:
                    await self._update_member_count_channel(guild, config["member_count_channel_id"])
            except Exception as e:
                logger.error(f"Błąd odświeżania licznika członków dla serwera {guild.id}: {e}", exc_info=True)

    @refresh_member_counts.before_loop
    async def before_refresh_member_counts(self) -> None:
        await self.bot.wait_until_ready()

    licznik_group = app_commands.Group(
        name="licznik",
        description="Licznik",
        default_permissions=discord.Permissions(administrator=True)
    )

    @licznik_group.command(name="ustaw_czlonkow", description="Ustawia kanał głosowy.")
    @app_commands.describe(kanal="Pusty kanał głosowy, którego nazwę bot będzie automatycznie aktualizował")
    @is_administrator()
    async def licznik_ustaw_czlonkow(self, interaction: discord.Interaction, kanal: discord.VoiceChannel) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, member_count_channel_id) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET member_count_channel_id = ?
            """,
            (interaction.guild_id, kanal.id, kanal.id)
        )
        await interaction.response.send_message(
            embed=success_embed("Zaktualizowano Ustawienia", f"Kanał {kanal.mention} będzie teraz pokazywał liczbę członków serwera (odświeżanie co ~10 minut).")
        )
        await self._update_member_count_channel(interaction.guild, kanal.id)

    @licznik_group.command(name="ustaw_ostatni", description="Ustawia kanał głosowy pok")
    @app_commands.describe(kanal="Pusty kanał głosowy, którego nazwę bot będzie automatycznie aktualizował")
    @is_administrator()
    async def licznik_ustaw_ostatni(self, interaction: discord.Interaction, kanal: discord.VoiceChannel) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, last_joined_channel_id) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET last_joined_channel_id = ?
            """,
            (interaction.guild_id, kanal.id, kanal.id)
        )
        await interaction.response.send_message(
            embed=success_embed("Zaktualizowano Ustawienia", f"Kanał {kanal.mention} będzie teraz pokazywał ostatnio dołączonego członka.")
        )

    @licznik_group.command(name="ustaw_role", description="Ustawia rolę, której członków")
    @app_commands.describe(rola="Rola, której posiadaczy ma liczyć licznik (np. Team77)")
    @is_administrator()
    async def licznik_ustaw_role(self, interaction: discord.Interaction, rola: discord.Role) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO guild_config (guild_id, member_count_role_id) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET member_count_role_id = ?
            """,
            (interaction.guild_id, rola.id, rola.id)
        )
        await interaction.response.send_message(
            embed=success_embed("Zaktualizowano Ustawienia", f"Licznik członków będzie teraz liczył wyłącznie osoby z rolą {rola.mention} ({len(rola.members)} obecnie).")
        )

        config = await self._get_config(interaction.guild_id)
        if config and config["member_count_channel_id"]:
            await self._update_member_count_channel(interaction.guild, config["member_count_channel_id"])

    @licznik_group.command(name="odswiez", description="Ręcznie wymusza ")
    @is_administrator()
    async def licznik_odswiez(self, interaction: discord.Interaction) -> None:
        config = await self._get_config(interaction.guild_id)
        if not config or not config["member_count_channel_id"]:
            await interaction.response.send_message(
                embed=error_embed("Kanał Nieustawiony", "Najpierw ustaw kanał komendą `/licznik ustaw_czlonkow`."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self._update_member_count_channel(interaction.guild, config["member_count_channel_id"])
        await interaction.followup.send(embed=success_embed("Odświeżono", "Licznik członków został ręcznie zaktualizowany."), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemberCounterCog(bot))