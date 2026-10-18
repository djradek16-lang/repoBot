"""
Moduł administracyjny właściciela bota (Bot Owner).
Umożliwia przeładowywanie modułów Cog, wyłączanie bota, ewaluację kodu, synchronizację oraz ogłoszenia globalne.
"""

import sys
import os
import io
import json
import logging
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from core.checks import is_owner
from utils.embeds import success_embed, error_embed, info_embed, create_embed
import config

logger = logging.getLogger("AegisBot.Cogs.Owner")

class OwnerCog(commands.Cog):
    """Wyłączne komendy dla Właściciela Bota."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    owner_group = app_commands.Group(
        name="owner",
        description="Komendy zastrzeżone dla właściciela bota",
        default_permissions=discord.Permissions(administrator=True)
    )

    @owner_group.command(name="reload", description="Przeładowuje podany rozszerzenie Cog lub wszystkie Cogi.")
    @app_commands.describe(cog_name="Nazwa modułu Cog (np. cogs.moderation) lub 'all'")
    @is_owner()
    async def reload_cog(self, interaction: discord.Interaction, cog_name: str) -> None:
        await interaction.response.defer(ephemeral=True)

        views_to_clear = [m for m in sys.modules if m.startswith("views.") or m == "views"]
        for mod in views_to_clear:
            del sys.modules[mod]

        if cog_name.lower() == "all":
            reloaded = []
            failed = []
            for ext in list(self.bot.extensions.keys()):
                try:
                    await self.bot.reload_extension(ext)
                    reloaded.append(ext)
                except Exception as e:
                    failed.append(f"{ext}: {e}")

            msg = f"**Przeładowano {len(reloaded)} modułów.**"
            if failed:
                msg += f"\n\n**Błędy:**\n" + "\n".join(failed)
            await interaction.followup.send(embed=success_embed("Przeładowano Cogi", msg))
        else:
            try:
                name = cog_name if cog_name.startswith("cogs.") else f"cogs.{cog_name}"
                if name in self.bot.extensions:
                    await self.bot.reload_extension(name)
                    msg = f"Pomyślnie przeładowano moduł `{name}` bez restartu bota."
                else:
                    await self.bot.load_extension(name)
                    msg = f"Pomyślnie załadowano nowy moduł `{name}` w locie (bez restartu bota)."
                await interaction.followup.send(embed=success_embed("Moduł Zaktualizowany", msg))
            except Exception as e:
                await interaction.followup.send(embed=error_embed("Błąd Operacji na Module", f"Nie udało się załadować/przeładować `{cog_name}`:\n`{e}`"))

    @owner_group.command(name="git_pull", description="Pobiera najnowsze pliki z repozytorium Git (git pull) bezpośrednio na hoście.")
    @app_commands.describe(
        reload_after="Czy automatycznie przeładować moduły Cog po pobraniu zmian",
        force="Wymusza zsynchronizowanie serwera z repozytorium (odrzuca lokalne edycje)"
    )
    @is_owner()
    async def git_pull(self, interaction: discord.Interaction, reload_after: bool = True, force: bool = True) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            if force:
                discard_proc = await asyncio.create_subprocess_exec(
                    "git", "checkout", "--", ".",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(config.BASE_DIR)
                )
                await discard_proc.communicate()

            proc = await asyncio.create_subprocess_exec(
                "git", "pull",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(config.BASE_DIR)
            )
            stdout, stderr = await proc.communicate()
            out_text = stdout.decode("utf-8", errors="replace").strip()
            err_text = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                await interaction.followup.send(
                    embed=error_embed("Błąd Git Pull", f"Komenda `git pull` zakończyła się błędem (kod {proc.returncode}):\n```\n{err_text or out_text}\n```")
                )
                return

            msg = f"**Wynik `git pull`:**\n```\n{out_text or 'Brak zmian (Already up to date).'}\n```"

            if reload_after:
                views_to_clear = [m for m in sys.modules if m.startswith("views.") or m == "views"]
                for mod in views_to_clear:
                    del sys.modules[mod]
                reloaded = []
                failed = []
                for ext in list(self.bot.extensions.keys()):
                    try:
                        await self.bot.reload_extension(ext)
                        reloaded.append(ext)
                    except Exception as re_err:
                        failed.append(f"{ext}: {re_err}")
                msg += f"\n\n🔄 **Przeładowano {len(reloaded)} modułów Cog.**"
                if failed:
                    msg += f"\n⚠️ **Błędy przeładowania:**\n" + "\n".join(failed)

            await interaction.followup.send(
                embed=success_embed("Aktualizacja z Gita Zakończona", msg)
            )
        except Exception as e:
            logger.error(f"Wyjątek podczas /owner git_pull: {e}", exc_info=True)
            await interaction.followup.send(
                embed=error_embed("Błąd Wykonania Git", f"Wystąpił błąd podczas wywołania gita:\n`{e}`")
            )

    @owner_group.command(name="sync", description="Wymusza pełną synchronizację komend slash z Discord API.")
    @is_owner()
    async def sync_commands(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            if interaction.guild:
                self.bot.tree.copy_global_to(guild=interaction.guild)
                synced_guild = await self.bot.tree.sync(guild=interaction.guild)
                logger.info("Błyskawicznie zsynchronizowano %s komend dla tej gildii.", len(synced_guild))
            synced = await self.bot.tree.sync()
            await interaction.followup.send(
                embed=success_embed(
                    "Synchronizacja Slash Commands",
                    f"✅ Zsynchronizowano **{len(synced)}** komend slash globalnie oraz **błyskawicznie na tym serwerze**!\n"
                    "Nowe komendy (w tym `/flota`) powinny być widoczne od razu (jeśli nie, wciśnij `Ctrl + R` na Discordzie).",
                )
            )
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Błąd Synchronizacji", f"Wystąpił błąd: {e}"))

    @owner_group.command(name="servers", description="Wyświetla listę połączonych serwerów Discord.")
    @is_owner()
    async def list_servers(self, interaction: discord.Interaction) -> None:
        guilds = self.bot.guilds
        lines = [f"• **{g.name}** (`{g.id}`) — Członków: `{g.member_count}`" for g in guilds]
        embed = info_embed(f"🌐 Połączone Serwery ({len(guilds)})", "\n".join(lines[:25]))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @owner_group.command(name="eval", description="Wykonuje kod Python na żywo w środowisku bota (Debug).")
    @app_commands.describe(code="Kod Python do ewaluacji")
    @is_owner()
    async def eval_code(self, interaction: discord.Interaction, code: str) -> None:
        await interaction.response.defer(ephemeral=True)
        old_stdout = sys.stdout
        redirected_output = sys.stdout = io.StringIO()

        try:
            exec_code = f"async def _eval_func():\n" + "\n".join(f"    {line}" for line in code.split("\n"))
            local_vars = {"bot": self.bot, "interaction": interaction, "discord": discord}
            exec(exec_code, local_vars)
            res = await local_vars["_eval_func"]()
            printed = redirected_output.getvalue()

            output_str = f"**Wynik:**\n```py\n{res}\n```" if res is not None else ""
            if printed:
                output_str += f"\n**Stdout:**\n```py\n{printed}\n```"

            await interaction.followup.send(embed=success_embed("Ewaluacja Kodu", output_str or "Kod wykonany pomyślnie (brak wyniku)."))
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Błąd Wykonania Kodu", f"```py\n{e}\n```"))
        finally:
            sys.stdout = old_stdout

    @owner_group.command(name="shutdown", description="Bezpiecznie wyłącza bota (Graceful Shutdown).")
    @is_owner()
    async def shutdown_bot(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=info_embed("Wyłączanie Bota", "Rozpoczynam zamykanie połączeń i bezpieczne wyłączanie..."), ephemeral=True)
        logger.info(f"Wywołano komendę wyłączenia bota przez właściciela: {interaction.user}")
        await self.bot.close()

    @owner_group.command(name="restart", description="Restartuje proces bota (Restart).")
    @is_owner()
    async def restart_bot(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=info_embed("Restart Bota", "Zamykanie połączeń i restartowanie procesu bota..."), ephemeral=True)
        logger.info(f"Wywołano komendę restartu bota przez właściciela: {interaction.user}")

        # Zapisz informację o kanale, żeby po ponownym starcie bot mógł
        # potwierdzić w tym samym miejscu, że restart się powiódł.
        try:
            signal_path = config.BASE_DIR / ".restart_signal.json"
            signal_path.write_text(json.dumps({
                "channel_id": interaction.channel_id,
                "user_id": interaction.user.id
            }))
        except Exception as e:
            logger.error(f"Nie udało się zapisać sygnału restartu: {e}")

        await self.bot.close()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    @owner_group.command(name="blacklist", description="Dodaje lub usuwa użytkownika z globalnej czarnej listy bota.")
    @app_commands.describe(entity_id="ID użytkownika lub serwera", reason="Powód wpisania na czarną listę")
    @is_owner()
    async def blacklist(self, interaction: discord.Interaction, entity_id: str, reason: str = "Brak powodu") -> None:
        try:
            eid = int(entity_id)
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Błąd", "ID musi być wartością liczbową."), ephemeral=True)
            return

        row = await self.bot.db.fetchone("SELECT 1 FROM blacklists WHERE entity_id = ?", (eid,))
        if row:
            await self.bot.db.execute("DELETE FROM blacklists WHERE entity_id = ?", (eid,))
            await interaction.response.send_message(embed=success_embed("Usunięto z Blacklisty", f"ID `{eid}` zostało usunięte z czarnej listy."))
        else:
            await self.bot.db.execute(
                "INSERT INTO blacklists (entity_id, entity_type, reason) VALUES (?, 'USER', ?)",
                (eid, reason)
            )
            await interaction.response.send_message(embed=success_embed("Dodano do Blacklisty", f"ID `{eid}` zostało wpisane na czarną listę z powodem: *{reason}*."))

    def _is_owner_or_admin(self, ctx: commands.Context) -> bool:
        if ctx.author.id == config.OWNER_ID:
            return True
        if ctx.guild and (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id):
            return True
        return False

    @commands.command(name="sync", help="Wymusza pełną synchronizację komend slash z Discord API.")
    async def prefix_sync(self, ctx: commands.Context) -> None:
        """Prefiksowa wersja !sync dla właściciela/administratora."""
        if not self._is_owner_or_admin(ctx):
            return
        status_msg = await ctx.send("⏳ Trwa natychmiastowa synchronizacja komend slash...")
        try:
            if ctx.guild:
                self.bot.tree.copy_global_to(guild=ctx.guild)
                synced_guild = await self.bot.tree.sync(guild=ctx.guild)
                logger.info("Błyskawicznie zsynchronizowano %s komend dla gildii %s.", len(synced_guild), ctx.guild.id)
            synced = await self.bot.tree.sync()
            await status_msg.edit(
                content=(
                    f"✅ Zsynchronizowano **{len(synced)}** komend slash globalnie oraz natychmiastowo na tym serwerze!\n"
                    f"Komendy `/flota`, `/pojazd_info` oraz `!flota` są już aktywne.\n"
                    f"*(Jeśli komenda `/flota` jeszcze nie podpowiada się na liście ukośnika, wciśnij `Ctrl + R` w aplikacji Discord)*"
                )
            )
        except Exception as e:
            await status_msg.edit(content=f"❌ Błąd synchronizacji: {e}")

    @commands.command(name="gitpull", aliases=["pull", "git_pull"], help="Pobiera najnowsze zmiany z repozytorium GitHub na serwer.")
    async def prefix_gitpull(self, ctx: commands.Context) -> None:
        """Prefiksowa wersja !gitpull pobierająca zmiany na żywo na hoście."""
        if not self._is_owner_or_admin(ctx):
            return
        status_msg = await ctx.send("🔄 Trwa pobieranie aktualizacji z Gita (`git pull`)...")
        try:
            discard_proc = await asyncio.create_subprocess_exec(
                "git", "checkout", "--", ".",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(config.BASE_DIR)
            )
            await discard_proc.communicate()

            proc = await asyncio.create_subprocess_exec(
                "git", "pull",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(config.BASE_DIR)
            )
            stdout, stderr = await proc.communicate()
            out_text = stdout.decode("utf-8", errors="replace").strip()
            err_text = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                await status_msg.edit(content=f"❌ Błąd `git pull`:\n```{err_text or out_text}```")
                return

            views_to_clear = [m for m in sys.modules if m.startswith("views.") or m == "views"]
            for mod in views_to_clear:
                del sys.modules[mod]

            reloaded = []
            failed = []
            for ext in list(self.bot.extensions.keys()):
                try:
                    await self.bot.reload_extension(ext)
                    reloaded.append(ext)
                except Exception as re_err:
                    failed.append(f"{ext}: {re_err}")

            if ctx.guild:
                self.bot.tree.copy_global_to(guild=ctx.guild)
                await self.bot.tree.sync(guild=ctx.guild)

            res_text = (
                f"✅ **Aktualizacja Git zakończona sukcesem!**\n"
                f"```{out_text or 'Brak zmian (Already up to date)'}```\n"
                f"🔄 Przeładowano **{len(reloaded)}** modułów Cog.\n"
                f"Błyskawicznie zsynchronizowano komendy dla tego serwera!"
            )
            if failed:
                res_text += f"\n⚠️ Błędy przeładowania:\n" + "\n".join(failed)

            await status_msg.edit(content=res_text)
        except Exception as e:
            await status_msg.edit(content=f"❌ Wyjątek podczas aktualizacji: {e}")

    @commands.command(name="reload", help="Przeładowuje moduły Cog.")
    async def prefix_reload(self, ctx: commands.Context, cog_name: str = "all") -> None:
        """Prefiksowa wersja !reload dla modułów."""
        if not self._is_owner_or_admin(ctx):
            return
        views_to_clear = [m for m in sys.modules if m.startswith("views.") or m == "views"]
        for mod in views_to_clear:
            del sys.modules[mod]

        if cog_name.lower() == "all":
            reloaded = []
            failed = []
            for ext in list(self.bot.extensions.keys()):
                try:
                    await self.bot.reload_extension(ext)
                    reloaded.append(ext)
                except Exception as e:
                    failed.append(f"{ext}: {e}")
            msg = f"🔄 Przeładowano **{len(reloaded)}** modułów Cog."
            if failed:
                msg += f"\n⚠️ Błędy:\n" + "\n".join(failed)
            await ctx.send(msg)
        else:
            name = cog_name if cog_name.startswith("cogs.") else f"cogs.{cog_name}"
            try:
                if name in self.bot.extensions:
                    await self.bot.reload_extension(name)
                    await ctx.send(f"✅ Przeładowano moduł `{name}`.")
                else:
                    await self.bot.load_extension(name)
                    await ctx.send(f"✅ Załadowano nowy moduł `{name}`.")
            except Exception as e:
                await ctx.send(f"❌ Błąd podczas ładowania `{name}`: `{e}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OwnerCog(bot))