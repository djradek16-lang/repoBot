"""
Interaktywne widoki Discord UI (przyciski, select menu, panele sędziowskie)
dla teleturnieju 'Jeden z dziesięciu' (1 z 10) na kanale głosowym i Scenie (Stage Channel).
"""

from __future__ import annotations
import time
from typing import TYPE_CHECKING, Optional
import discord
from discord import ui

if TYPE_CHECKING:
    from cogs.one_out_of_ten import OneOutOfTenGame, Player

class RegistrationView(ui.View):
    """Widok rejestracji graczy i wyboru stanowisk (Lobby)."""

    def __init__(self, game: OneOutOfTenGame) -> None:
        super().__init__(timeout=None)
        self.game = game

    @ui.button(label="Zajmij stanowisko", style=discord.ButtonStyle.success, emoji="🎯", custom_id="1z10:join")
    async def join_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Wystąpił błąd użytkownika.", ephemeral=True)
            return

        if self.game.is_player(member.id):
            await interaction.response.send_message("Jesteś już zapisany do teleturnieju!", ephemeral=True)
            return

        if len(self.game.players) >= 10:
            await interaction.response.send_message("Wszystkie 10 stanowisk jest już zajętych!", ephemeral=True)
            return

        player = self.game.add_player(member)
        # Jeśli jesteśmy na kanale Stage (Scena), zaproś gracza lub znieś wyciszenie (jeśli możliwe)
        if isinstance(self.game.voice_channel, discord.StageChannel):
            try:
                if member.voice and member.voice.channel == self.game.voice_channel:
                    await member.edit(suppress=False)
            except Exception:
                pass

        await interaction.response.send_message(
            f"✅ Zajęto stanowisko **#{player.slot}**! Przygotuj się do gry.",
            ephemeral=True
        )
        await self.game.update_public_board()

    @ui.button(label="Opuść stanowisko", style=discord.ButtonStyle.secondary, emoji="🚪", custom_id="1z10:leave")
    async def leave_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        member = interaction.user
        if not self.game.is_player(member.id):
            await interaction.response.send_message("Nie jesteś zapisany na żadnym stanowisku.", ephemeral=True)
            return

        removed_slot = self.game.remove_player(member.id)
        if isinstance(self.game.voice_channel, discord.StageChannel):
            try:
                if isinstance(member, discord.Member) and member.voice and member.voice.channel == self.game.voice_channel:
                    await member.edit(suppress=True)
            except Exception:
                pass

        await interaction.response.send_message(
            f"🚪 Zwolniono stanowisko **#{removed_slot}**.",
            ephemeral=True
        )
        await self.game.update_public_board()

    @ui.button(label="Rozpocznij teleturniej", style=discord.ButtonStyle.primary, emoji="▶️", custom_id="1z10:start_game")
    async def start_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.game.host.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko Prowadzący (lub Administrator) może wystartować grę!", ephemeral=True)
            return

        if len(self.game.players) < 2:
            await interaction.response.send_message("Do rozpoczęcia gry potrzeba co najmniej 2 uczestników!", ephemeral=True)
            return

        await interaction.response.defer()
        await self.game.start_stage_1()

    @ui.button(label="Anuluj turniej", style=discord.ButtonStyle.danger, emoji="❌", custom_id="1z10:cancel_game")
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.game.host.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko Prowadzący może anulować event.", ephemeral=True)
            return

        await interaction.response.send_message("🛑 Teleturniej został anulowany przez organizatora.", ephemeral=False)
        await self.game.stop()


class BuzzerView(ui.View):
    """Widok przycisku 'ZGŁASZAM SIĘ!' (Buzzer) w rundach otwartych."""

    def __init__(self, game: OneOutOfTenGame) -> None:
        super().__init__(timeout=None)
        self.game = game

    @ui.button(label="ZGŁASZAM SIĘ!", style=discord.ButtonStyle.danger, emoji="🔔", custom_id="1z10:buzzer_press")
    async def buzzer_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        member = interaction.user
        player = self.game.get_player(member.id)

        if not player or not player.is_active:
            await interaction.response.send_message("Nie jesteś aktywnym uczestnikiem tej rundy!", ephemeral=True)
            return

        if not self.game.buzzer_active:
            await interaction.response.send_message("Buzzer nie jest w tej chwili aktywny!", ephemeral=True)
            return

        reaction_time = time.perf_counter() - (self.game.buzzer_start_time or time.perf_counter())
        success = await self.game.handle_buzzer_press(player, reaction_time)
        if success:
            await interaction.response.send_message(
                f"🔔 Zgłoszono jako pierwszy! Czas reakcji: `{reaction_time:.3f}s`.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("Inny gracz był ułamek sekundy szybszy!", ephemeral=True)

    @ui.button(label="Panel Prowadzącego", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="1z10:open_host_panel")
    async def host_panel_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.game.host.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Ten panel jest dostępny wyłącznie dla Prowadzącego teleturniej!", ephemeral=True)
            return

        embed = self.game.build_host_embed()
        view = HostControlView(self.game)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class NominationView(ui.View):
    """Widok wyboru rywala lub wzięcia pytania na siebie (dla gracza nominującego)."""

    def __init__(self, game: OneOutOfTenGame, nominator: Player) -> None:
        super().__init__(timeout=120)
        self.game = game
        self.nominator = nominator

        # Przycisk "Biorę na siebie" (20 pkt)
        take_self_btn = ui.Button(
            label="Biorę na siebie! (20 pkt)",
            style=discord.ButtonStyle.success,
            emoji="⭐",
            custom_id=f"1z10:nominate:self:{nominator.member.id}"
        )
        take_self_btn.callback = self.take_on_self_callback
        self.add_item(take_self_btn)

        # Select menu z aktywnymi rywalami (za 10 pkt)
        active_rivals = [p for p in game.players if p.is_active and p.slot != nominator.slot]
        if active_rivals:
            options = [
                discord.SelectOption(
                    label=f"Stanowisko {p.slot}: {p.member.display_name[:20]}",
                    value=str(p.slot),
                    description=f"Szanse: {'❤️' * p.chances} | Punkty: {p.score}",
                    emoji="👉"
                )
                for p in active_rivals
            ]
            select_menu = ui.Select(
                placeholder="Wskaż rywala do odpowiedzi (10 pkt)...",
                options=options,
                custom_id=f"1z10:nominate:rival_select:{nominator.member.id}"
            )
            select_menu.callback = self.rival_selected_callback
            self.add_item(select_menu)

    async def take_on_self_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.nominator.member.id and interaction.user.id != self.game.host.id:
            await interaction.response.send_message("Tylko gracz wyznaczający lub Prowadzący może dokonać wyboru!", ephemeral=True)
            return

        await interaction.response.defer()
        await self.game.nominate_player(self.nominator, points_value=20)

    async def rival_selected_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.nominator.member.id and interaction.user.id != self.game.host.id:
            await interaction.response.send_message("Tylko gracz wyznaczający lub Prowadzący może dokonać wyboru!", ephemeral=True)
            return

        select_item = interaction.data.get("values", [None])[0]  # type: ignore
        if not select_item:
            return

        target_slot = int(select_item)
        target_player = self.game.get_player_by_slot(target_slot)
        if not target_player or not target_player.is_active:
            await interaction.response.send_message("Ten gracz nie jest już aktywny.", ephemeral=True)
            return

        await interaction.response.defer()
        await self.game.nominate_player(target_player, points_value=10)


class ManualSelectView(ui.View):
    """Widok ręcznego wyboru gracza do odpowiedzi przez Prowadzącego."""

    def __init__(self, game: OneOutOfTenGame, players: list[Player]) -> None:
        super().__init__(timeout=60)
        self.game = game
        options = [
            discord.SelectOption(
                label=f"Stanowisko {p.slot}: {p.member.display_name[:20]}",
                value=str(p.slot),
                description=f"Szanse: {'❤️' * p.chances} | Punkty: {p.score}",
                emoji="👉"
            )
            for p in players
        ]
        select = ui.Select(placeholder="Wskaż stanowisko do odpowiedzi...", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction) -> None:
        select_item = interaction.data.get("values", [None])[0]  # type: ignore
        if select_item:
            slot = int(select_item)
            target = self.game.get_player_by_slot(slot)
            if target:
                await self.game.set_responder(target, points_value=10)
                await interaction.response.send_message(
                    f"Wyznaczono stanowisko #{slot} ({target.member.mention}).",
                    ephemeral=True
                )
                await self.game.update_public_board()


class HostControlView(ui.View):
    """Panel sędziowski Prowadzącego (Tadeusza Sznuka)."""

    def __init__(self, game: OneOutOfTenGame) -> None:
        super().__init__(timeout=None)
        self.game = game

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.host.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko Prowadzący ma dostęp do tego panelu!", ephemeral=True)
            return False
        return True

    @ui.button(label="Poprawna", style=discord.ButtonStyle.success, emoji="✅", row=0)
    async def correct_answer_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not self.game.current_responder:
            await interaction.response.send_message("Żaden gracz nie jest obecnie przy głosie!", ephemeral=True)
            return

        await interaction.response.defer()
        await self.game.handle_answer(is_correct=True)
        await interaction.edit_original_response(embed=self.game.build_host_embed(), view=self)

    @ui.button(label="Błędna", style=discord.ButtonStyle.danger, emoji="❌", row=0)
    async def wrong_answer_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not self.game.current_responder:
            await interaction.response.send_message("Żaden gracz nie jest obecnie przy głosie!", ephemeral=True)
            return

        await interaction.response.defer()
        await self.game.handle_answer(is_correct=False)
        await interaction.edit_original_response(embed=self.game.build_host_embed(), view=self)

    @ui.button(label="Otwórz Buzzer", style=discord.ButtonStyle.primary, emoji="🔔", row=0)
    async def open_buzzer_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer()
        await self.game.activate_buzzer()
        await interaction.edit_original_response(embed=self.game.build_host_embed(), view=self)

    @ui.button(label="Następne pytanie", style=discord.ButtonStyle.secondary, emoji="⏭️", row=1)
    async def next_question_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer()
        self.game.next_question()
        await self.game.update_public_board()
        await interaction.edit_original_response(embed=self.game.build_host_embed(), view=self)

    @ui.button(label="Gong czasu", style=discord.ButtonStyle.secondary, emoji="🔊", row=1)
    async def gong_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.game.play_sound("gong")
        await interaction.response.send_message("🔊 Odtworzono gong upływu czasu!", ephemeral=True)

    @ui.button(label="Wyznacz gracza ręcznie", style=discord.ButtonStyle.secondary, emoji="🎯", row=1)
    async def manual_nominate_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        active_players = [p for p in self.game.players if p.is_active]
        if not active_players:
            await interaction.response.send_message("Brak aktywnych graczy.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Wskaż gracza, który ma odpowiadać na Scenie:",
            view=ManualSelectView(self.game, active_players),
            ephemeral=True
        )

    @ui.button(label="+1 Szansa", style=discord.ButtonStyle.success, emoji="➕", row=2)
    async def add_chance_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.game.current_responder:
            self.game.current_responder.chances = min(3, self.game.current_responder.chances + 1)
            self.game.current_responder.is_active = True
            await self.game.update_public_board()
            await interaction.response.send_message(f"Dodano szansę dla stanowiska #{self.game.current_responder.slot}.", ephemeral=True)
            await interaction.edit_original_response(embed=self.game.build_host_embed(), view=self)
        else:
            await interaction.response.send_message("Wybierz najpierw gracza przy głosie.", ephemeral=True)

    @ui.button(label="-1 Szansa", style=discord.ButtonStyle.danger, emoji="➖", row=2)
    async def remove_chance_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if self.game.current_responder:
            self.game.current_responder.chances = max(0, self.game.current_responder.chances - 1)
            if self.game.current_responder.chances == 0:
                self.game.current_responder.is_active = False
            await self.game.update_public_board()
            await interaction.response.send_message(f"Odjęto szansę dla stanowiska #{self.game.current_responder.slot}.", ephemeral=True)
            await interaction.edit_original_response(embed=self.game.build_host_embed(), view=self)
        else:
            await interaction.response.send_message("Wybierz najpierw gracza przy głosie.", ephemeral=True)

    @ui.button(label="Zakończ turniej", style=discord.ButtonStyle.danger, emoji="🛑", row=2)
    async def stop_game_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.send_message("🛑 Kończenie turnieju...", ephemeral=True)
        await self.game.finish_game(force=True)
