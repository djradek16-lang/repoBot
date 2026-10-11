"""
Moduł teleturnieju 'Jeden z dziesięciu' (1 z 10) dla serwerów Discord.
Obsługuje kanały głosowe (Voice) oraz Sceny (Stage Channels), panel sędziowski
dla Prowadzącego, system buzzerów w czasie rzeczywistym, wyznaczanie rywali,
biorę na siebie, automatyczne odtwarzanie efektów dźwiękowych oraz bazę pytań.
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from pathlib import Path
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.embeds import create_embed, success_embed, error_embed, info_embed
from utils.sound_generator import generate_default_sounds
from views.one_out_of_ten_views import (
    RegistrationView,
    BuzzerView,
    NominationView,
    HostControlView
)

logger = logging.getLogger("AegisBot.Cogs.OneOutOfTen")

QUESTIONS_FILE = config.BASE_DIR / "data" / "1z10_questions.json"
SOUNDS_DIR = config.BASE_DIR / "data" / "sounds_1z10"


@dataclass
class Player:
    """Reprezentacja uczestnika na stanowisku teleturnieju 1 z 10."""
    member: discord.Member
    slot: int  # Numer stanowiska 1..10
    chances: int = 3
    score: int = 0
    is_active: bool = True
    stage1_correct: int = 0
    buzz_correct_stage3: int = 0
    reaction_time: Optional[float] = None

    @property
    def chances_str(self) -> str:
        return ("❤️" * max(0, self.chances)) + ("🤍" * max(0, 3 - self.chances))


class OneOutOfTenGame:
    """Główna maszyna stanu teleturnieju 1 z 10."""

    def __init__(
        self,
        cog: OneOutOfTenCog,
        guild: discord.Guild,
        channel: discord.TextChannel,
        voice_channel: discord.VoiceChannel | discord.StageChannel,
        host: discord.Member,
    ) -> None:
        self.cog = cog
        self.bot = cog.bot
        self.guild = guild
        self.channel = channel
        self.voice_channel = voice_channel
        self.host = host

        self.stage: str = "REGISTRATION"  # REGISTRATION, STAGE_1, STAGE_2, STAGE_3, FINISHED
        self.players: list[Player] = []
        self.current_question: Optional[dict] = None
        self.current_responder: Optional[Player] = None
        self.current_nominator: Optional[Player] = None
        self.current_points_value: int = 10

        # Etap I
        self.stage1_turn: int = 1  # Kolejka 1 lub 2
        self.stage1_idx: int = 0

        # Etap II & III - Buzzer
        self.buzzer_active: bool = False
        self.buzzer_start_time: Optional[float] = None

        self.public_message: Optional[discord.Message] = None
        self.used_question_ids: set[int] = set()

    # --- Zarządzanie Uczestnikami ---

    def add_player(self, member: discord.Member) -> Player:
        used_slots = {p.slot for p in self.players}
        available_slot = 1
        for s in range(1, 11):
            if s not in used_slots:
                available_slot = s
                break

        player = Player(member=member, slot=available_slot)
        self.players.append(player)
        self.players.sort(key=lambda x: x.slot)
        return player

    def remove_player(self, member_id: int) -> Optional[int]:
        for i, p in enumerate(self.players):
            if p.member.id == member_id:
                slot = p.slot
                self.players.pop(i)
                return slot
        return None

    def get_player(self, member_id: int) -> Optional[Player]:
        for p in self.players:
            if p.member.id == member_id:
                return p
        return None

    def get_player_by_slot(self, slot: int) -> Optional[Player]:
        for p in self.players:
            if p.slot == slot:
                return p
        return None

    def is_player(self, member_id: int) -> bool:
        return any(p.member.id == member_id for p in self.players)

    @property
    def active_players(self) -> list[Player]:
        return [p for p in self.players if p.is_active]

    # --- Efekty Dźwiękowe na VC / Scenie ---

    def play_sound(self, sound_name: str) -> None:
        vc = self.guild.voice_client
        if not vc or not vc.is_connected():
            return

        file_path = None
        for ext in (".mp3", ".wav"):
            cand = SOUNDS_DIR / f"{sound_name}{ext}"
            if cand.exists():
                file_path = cand
                break

        if not file_path:
            return

        try:
            if vc.is_playing():
                vc.stop()
            ffmpeg_bin = "C:\\FFMPEG\\bin\\ffmpeg.exe" if Path("C:\\FFMPEG\\bin\\ffmpeg.exe").exists() else "ffmpeg"
            source = discord.FFmpegPCMAudio(str(file_path), executable=ffmpeg_bin)
            vc.play(source)
        except Exception as e:
            logger.error(f"Błąd odtwarzania dźwięku {sound_name}: {e}")

    # --- Pytania ---

    def next_question(self) -> dict:
        questions = self.cog.questions
        if not questions:
            self.current_question = {
                "id": 0,
                "category": "Ogólna",
                "question": "Brak pytań w bazie! Dodaj pytania komendą /1z10 pytania dodaj.",
                "answer": "Brak"
            }
            return self.current_question

        available = [q for q in questions if q.get("id") not in self.used_question_ids]
        if not available:
            self.used_question_ids.clear()
            available = questions

        chosen = random.choice(available)
        self.used_question_ids.add(chosen.get("id", 0))
        self.current_question = chosen
        return chosen

    # --- Budowanie Embedów ---

    def build_public_embed(self) -> discord.Embed:
        stage_names = {
            "REGISTRATION": "Lobby • Zapisy na stanowiska",
            "STAGE_1": f"Etap I • Eliminacje (Kolejka {self.stage1_turn}/2)",
            "STAGE_2": "Etap II • Pojedynek i Wyznaczanie",
            "STAGE_3": "Etap III • WIELKI FINAŁ",
            "FINISHED": "Koniec Turnieju • Wyniki i Podium"
        }

        embed = create_embed(
            title="🎙️ TELETURNIEJ: JEDEN Z DZIESIĘCIU",
            description=f"**Aktualna Faza:** `{stage_names.get(self.stage, self.stage)}`\n"
                        f"**Prowadzący:** {self.host.mention} | **Kanał:** {self.voice_channel.mention}\n"
                        f"{'─' * 42}",
            color=0xD4AF37  # Szlachetne złoto studia 1 z 10
        )

        # Prezentacja stanowisk graczy
        if self.players:
            lines = []
            for p in self.players:
                status_icon = "🟢" if p.is_active else "❌ Odpadł"
                if self.current_responder and self.current_responder.slot == p.slot:
                    status_icon = "🎙️ **PRZY GŁOSIE**"
                elif self.current_nominator and self.current_nominator.slot == p.slot:
                    status_icon = "👉 **WYBIERA RYWALA**"

                name = p.member.display_name[:18]
                lines.append(f"`[{p.slot:02d}]` **{name}** | {p.chances_str} | **{p.score}** pkt | {status_icon}")

            embed.add_field(
                name=f"Stanowiska Uczestników ({len(self.active_players)}/{len(self.players)} aktywnych)",
                value="\n".join(lines),
                inline=False
            )
        else:
            embed.add_field(
                name="Stanowiska Uczestników (0/10)",
                value="*Brak zapisanych graczy. Kliknij przycisk poniżej, aby zająć stanowisko!*",
                inline=False
            )

        # Informacje o aktualnym pytaniu
        if self.stage in ("STAGE_1", "STAGE_2", "STAGE_3") and self.current_question:
            cat = self.current_question.get("category", "Wiedza Ogólna")
            q_text = self.current_question.get("question", "Brak treści")
            q_id = self.current_question.get("id", "?")

            val_str = f" (+{self.current_points_value} pkt)" if self.current_points_value else ""
            responder_str = f"Stanowisko #{self.current_responder.slot} ({self.current_responder.member.mention}){val_str}" if self.current_responder else "Pytanie otwarte (Kto pierwszy na buzzerze!)"

            embed.add_field(
                name=f"❓ Pytanie #{q_id} • Kategoria: {cat}",
                value=f"**{q_text}**\n\n🎯 **Odpowiada:** {responder_str}",
                inline=False
            )
        elif self.stage == "REGISTRATION":
            embed.add_field(
                name="📋 Zasady zapisu",
                value="• Zapisy są otwarte dla każdego obecnego na kanale głosowym/scenie.\n"
                      "• Maksymalnie 10 graczy (stanowiska 1 do 10).\n"
                      "• Prowadzący rozpoczyna turniej po zebraniu min. 2 graczy.",
                inline=False
            )

        return embed

    def build_host_embed(self) -> discord.Embed:
        """Tworzy embed widoczny wyłącznie dla Prowadzącego (Tadeusza Sznuka)."""
        embed = create_embed(
            title="📋 Panel Sędziowski Prowadzącego (Tadeusz Sznuk)",
            description=f"**Faza:** `{self.stage}` | **Pytanie aktywne:** "
                        f"{'Tak' if self.current_question else 'Nie'}\n"
                        f"{'─' * 42}",
            color=0x2B2D31
        )

        if self.current_question:
            embed.add_field(
                name="💡 PRAWIDŁOWA ODPOWIEDŹ",
                value=f"```{self.current_question.get('answer', 'Brak')}```",
                inline=False
            )
            embed.add_field(
                name="Treść Pytania",
                value=f"**[{self.current_question.get('category')}]** {self.current_question.get('question')}",
                inline=False
            )

        if self.current_responder:
            embed.add_field(
                name="🎙️ Gracz odpowiadający",
                value=f"Stanowisko **#{self.current_responder.slot}**: {self.current_responder.member.mention}\n"
                      f"Szanse: {self.current_responder.chances_str} | Punkty: {self.current_responder.score}\n"
                      f"Stawka pytania: **{self.current_points_value} pkt**",
                inline=True
            )
        elif self.buzzer_active:
            embed.add_field(
                name="🔔 Status Buzzera",
                value="**Buzzer jest OTWARTY!** Oczekiwanie na najszybszego gracza...",
                inline=True
            )

        if self.current_nominator:
            embed.add_field(
                name="👉 Wyznaczający",
                value=f"Stanowisko **#{self.current_nominator.slot}**: {self.current_nominator.member.mention}",
                inline=True
            )

        return embed

    async def update_public_board(self) -> None:
        """Odświeża publiczną wiadomość z tablicą wyników."""
        if not self.public_message:
            return

        embed = self.build_public_embed()
        view = None

        if self.stage == "REGISTRATION":
            view = RegistrationView(self)
        elif self.stage in ("STAGE_1", "STAGE_2", "STAGE_3"):
            if self.buzzer_active:
                view = BuzzerView(self)
            elif self.current_nominator and not self.current_responder:
                view = NominationView(self, self.current_nominator)
            else:
                # W trakcie pytania - przycisk dla prowadzącego do otwarcia panelu
                view = BuzzerView(self)
        elif self.stage == "FINISHED":
            view = None

        try:
            await self.public_message.edit(embed=embed, view=view)
        except discord.HTTPException as e:
            logger.error(f"Nie udało się zaktualizować tablicy 1z10: {e}")

    # --- Przebieg Teleturnieju ---

    async def start_stage_1(self) -> None:
        """Rozpoczyna Etap I (Eliminacje)."""
        self.stage = "STAGE_1"
        self.stage1_turn = 1
        self.stage1_idx = 0
        self.play_sound("intro")

        # Zaproś wszystkich graczy na scenę (jeśli to StageChannel)
        if isinstance(self.voice_channel, discord.StageChannel):
            for p in self.players:
                try:
                    if p.member.voice and p.member.voice.channel == self.voice_channel:
                        await p.member.edit(suppress=False)
                except Exception:
                    pass

        self.current_points_value = 10
        self.current_responder = self.players[0]
        self.next_question()

        await self.channel.send(
            embed=info_embed(
                "Etap I: Rozpoczęty!",
                "🎙️ **Prowadzący zadaje pytania każdemu uczestnikowi po kolei.**\n"
                "Każdy gracz musi odpowiedzieć w 2 kolejkach pytań.\n"
                "Powodzenia!"
            )
        )
        await self.update_public_board()

    async def advance_stage_1(self) -> None:
        """Przechodzi do kolejnego gracza w Etapie I lub kończy etap."""
        self.stage1_idx += 1
        active = [p for p in self.players if p.is_active]

        if not active:
            await self.finish_game(force=True)
            return

        # Jeśli przeszliśmy przez wszystkich aktywnych graczy w tej kolejce
        if self.stage1_idx >= len(self.players):
            if self.stage1_turn == 1:
                # Rozpocznij drugą kolejkę
                self.stage1_turn = 2
                self.stage1_idx = 0
                await self.channel.send(
                    embed=info_embed("Etap I: Druga Kolejka", "Rozpoczynamy drugą turę pytań dla każdego stanowiska!")
                )
            else:
                # Koniec Etapu I
                await self.start_stage_2()
                return

        # Znajdź najbliższego aktywnego gracza
        while self.stage1_idx < len(self.players) and not self.players[self.stage1_idx].is_active:
            self.stage1_idx += 1

        if self.stage1_idx >= len(self.players):
            if self.stage1_turn == 1:
                self.stage1_turn = 2
                self.stage1_idx = 0
            else:
                await self.start_stage_2()
                return

        self.current_responder = self.players[self.stage1_idx]
        self.current_points_value = 10
        self.next_question()
        await self.update_public_board()

    async def start_stage_2(self) -> None:
        """Rozpoczyna Etap II (Pojedynek i Wyznaczanie)."""
        active = self.active_players
        if len(active) <= 3:
            # Mniej lub równo 3 graczy - przechodzimy bezpośrednio do finału!
            await self.start_stage_3()
            return

        self.stage = "STAGE_2"
        self.current_responder = None
        self.current_nominator = None
        self.next_question()
        await self.channel.send(
            embed=info_embed(
                "Etap II: Pojedynek!",
                f"Do Etapu II zakwalifikowało się **{len(active)}** uczestników.\n"
                "Pytanie otwarte! Kto pierwszy wciśnie przycisk `[ ZGŁASZAM SIĘ! ]`, odpowiada na pytanie.\n"
                "Poprawna odpowiedź daje prawo do wyznaczania rywali lub brania pytań na siebie!"
            )
        )
        await self.activate_buzzer()

    async def activate_buzzer(self) -> None:
        """Aktywuje przycisk buzzera dla graczy."""
        self.buzzer_active = True
        self.buzzer_start_time = time.perf_counter()
        self.current_responder = None
        await self.update_public_board()

    async def handle_buzzer_press(self, player: Player, reaction_time: float) -> bool:
        """Obsługuje wciśnięcie buzzera przez gracza."""
        if not self.buzzer_active:
            return False

        self.buzzer_active = False
        self.current_responder = player
        self.current_points_value = 10
        player.reaction_time = reaction_time

        self.play_sound("buzzer")

        # Jeśli to kanał Sceny (Stage Channel), pozwól graczowi mówić
        if isinstance(self.voice_channel, discord.StageChannel):
            try:
                if player.member.voice and player.member.voice.channel == self.voice_channel:
                    await player.member.edit(suppress=False)
            except Exception:
                pass

        await self.channel.send(
            f"🔔 Stanowisko **#{player.slot}** ({player.member.mention}) zgłasza się jako pierwsze! (Czas reakcji: `{reaction_time:.3f}s`)"
        )
        await self.update_public_board()
        return True

    async def set_responder(self, target: Player, points_value: int = 10) -> None:
        """Ręczne lub automatyczne ustawienie gracza do odpowiedzi."""
        self.current_responder = target
        self.current_points_value = points_value
        self.current_nominator = None
        self.buzzer_active = False

        # Jeśli to Scena (Stage), znieś wyciszenie dla odpowiadającego gracza
        if isinstance(self.voice_channel, discord.StageChannel):
            try:
                if target.member.voice and target.member.voice.channel == self.voice_channel:
                    await target.member.edit(suppress=False)
            except Exception:
                pass

        self.next_question()

    async def nominate_player(self, target: Player, points_value: int) -> None:
        """Uczestnik wyznacza rywala lub bierze pytanie na siebie."""
        is_self = (self.current_nominator and self.current_nominator.slot == target.slot)
        desc = f"⭐ Stanowisko **#{target.slot}** ({target.member.mention}) bierze pytanie **NA SIEBIE** (za **20 pkt**)!" if is_self else f"👉 Stanowisko **#{self.current_nominator.slot}** wyznacza stanowisko **#{target.slot}** ({target.member.mention}) do odpowiedzi (za **10 pkt**)."

        await self.channel.send(desc)
        await self.set_responder(target, points_value)
        await self.update_public_board()

    async def handle_answer(self, is_correct: bool) -> None:
        """Obsługuje werdykt Prowadzącego (Poprawna / Błędna)."""
        responder = self.current_responder
        if not responder:
            return

        if is_correct:
            self.play_sound("correct")
            responder.score += self.current_points_value

            if self.stage == "STAGE_1":
                responder.stage1_correct += 1
                await self.channel.send(f"✅ Poprawna odpowiedź! Stanowisko **#{responder.slot}** zdobywa **{self.current_points_value} pkt**.")
                await self.advance_stage_1()

            elif self.stage in ("STAGE_2", "STAGE_3"):
                await self.channel.send(f"✅ Poprawna odpowiedź! Stanowisko **#{responder.slot}** zdobywa **{self.current_points_value} pkt**.")

                if self.stage == "STAGE_3":
                    responder.buzz_correct_stage3 += 1

                # W Etapie II lub w Etapie III (gdy odblokowano nominacje min. 3 trafienia w finale)
                can_nominate = True
                if self.stage == "STAGE_3" and responder.buzz_correct_stage3 < 3:
                    can_nominate = False

                if can_nominate:
                    self.current_nominator = responder
                    self.current_responder = None
                    await self.channel.send(
                        f"🎯 Stanowisko **#{responder.slot}** ({responder.member.mention}) ma prawo wyznaczyć rywala lub wziąć pytanie na siebie!"
                    )
                    await self.update_public_board()
                else:
                    # Jeszcze faza pytań z buzzera w finale
                    left = 3 - responder.buzz_correct_stage3
                    await self.channel.send(
                        f"ℹ️ Stanowisko **#{responder.slot}** potrzebuje jeszcze **{left}** trafień z buzzera, aby odblokować wyznaczanie!"
                    )
                    self.next_question()
                    await self.activate_buzzer()

        else:
            self.play_sound("wrong")
            responder.chances = max(0, responder.chances - 1)
            await self.channel.send(f"❌ Błędna odpowiedź! Stanowisko **#{responder.slot}** traci szansę ({responder.chances_str}).")

            if responder.chances == 0:
                responder.is_active = False
                await self.channel.send(f"☠️ Stanowisko **#{responder.slot}** ({responder.member.mention}) straciło wszystkie szanse i odpada z gry!")

                # Przenieś gracza na widownię Sceny (suppress=True)
                if isinstance(self.voice_channel, discord.StageChannel):
                    try:
                        if responder.member.voice and responder.member.voice.channel == self.voice_channel:
                            await responder.member.edit(suppress=True)
                    except Exception:
                        pass

            # Sprawdź przejście do kolejnych etapów
            active = self.active_players

            if self.stage == "STAGE_1":
                await self.advance_stage_1()

            elif self.stage == "STAGE_2":
                if len(active) <= 3:
                    await self.start_stage_3()
                    return
                # Po błędzie w Etapie II pytanie wraca na buzzer
                self.current_nominator = None
                self.next_question()
                await self.activate_buzzer()

            elif self.stage == "STAGE_3":
                if len(active) <= 1:
                    await self.finish_game()
                    return
                self.current_nominator = None
                self.next_question()
                await self.activate_buzzer()

    async def start_stage_3(self) -> None:
        """Rozpoczyna Etap III (Wielki Finał z udziałem 3 najlepszych graczy)."""
        finalists = self.active_players
        if len(finalists) == 0:
            await self.finish_game(force=True)
            return

        self.stage = "STAGE_3"
        self.play_sound("intro")

        # Bonus za zachowane szanse z poprzednich etapów (10 pkt za szansę) + reset do 3 szans na finał
        for f in finalists:
            bonus = f.chances * 10
            f.score += bonus
            f.chances = 3
            f.buzz_correct_stage3 = 0

        names = ", ".join(f"**#{f.slot} {f.member.display_name}**" for f in finalists)
        await self.channel.send(
            embed=info_embed(
                "🏆 WIELKI FINAŁ TELETURNIEJU!",
                f"Oto nasi wspaniali finaliści: {names}!\n\n"
                "• Każdy finalista otrzymał bonus punktowy za zachowane szanse (+10 pkt/szansa).\n"
                "• Każdy finalista otrzymuje nowe **3 szanse** (`❤️❤️❤️`)!\n"
                "• Zaczynamy od pytań na zgłoszenie (Buzzer).\n"
                "• Uczestnik, który odpowie poprawnie na 3 pytania z buzzera, odblokowuje wyznaczanie rywali!"
            )
        )

        self.current_responder = None
        self.current_nominator = None
        self.next_question()
        await self.activate_buzzer()

    async def finish_game(self, force: bool = False) -> None:
        """Kończy teleturniej, dolicza punkty za zachowane szanse i ogłasza podium."""
        self.stage = "FINISHED"
        self.play_sound("intro")

        # Dolicz punkty za pozostałe szanse w finale
        for p in self.players:
            if p.chances > 0:
                p.score += p.chances * 10

        # Sortowanie według punktów
        ranking = sorted(self.players, key=lambda x: x.score, reverse=True)

        medals = ["🥇", "🥈", "🥉"]
        ranking_lines = []
        for i, p in enumerate(ranking):
            medal = medals[i] if i < len(medals) else f"#{i+1}"
            ranking_lines.append(f"{medal} **{p.member.display_name}** (Stanowisko #{p.slot}) — **{p.score} pkt** | Szanse: {p.chances_str}")

        winner_name = ranking[0].member.display_name if ranking else "Brak"

        finish_embed = create_embed(
            title="🏆 WYNIKI KOŃCOWE TELETURNIEJU 1 Z 10",
            description=f"Gratulacje dla wszystkich uczestników!\n"
                        f"Zwycięzcą turnieju zostaje **{winner_name}**!\n\n"
                        f"{chr(10).join(ranking_lines)}",
            color=0xFEE75C
        )

        await self.channel.send(embed=finish_embed)
        await self.update_public_board()
        await self.stop()

    async def stop(self) -> None:
        """Zatrzymuje grę i rozłącza bota z kanału głosowego/sceny."""
        self.cog.games.pop(self.guild.id, None)

        if isinstance(self.voice_channel, discord.StageChannel):
            try:
                if self.voice_channel.instance:
                    await self.voice_channel.instance.delete()
            except Exception:
                pass

        vc = self.guild.voice_client
        if vc and vc.is_connected():
            try:
                await vc.disconnect()
            except Exception:
                pass


class OneOutOfTenCog(commands.Cog, name="1 z 10"):
    """Moduł organizacji turnieju 1 z 10 na kanale głosowym lub Scenie."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.games: dict[int, OneOutOfTenGame] = {}  # guild_id -> Game
        self.questions: list[dict] = []
        self._load_questions()
        # Przygotuj dźwięki startowe
        generate_default_sounds(SOUNDS_DIR)

    def _load_questions(self) -> None:
        if QUESTIONS_FILE.exists():
            try:
                with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
                    self.questions = json.load(f)
                logger.info(f"Załadowano {len(self.questions)} pytań do teleturnieju 1 z 10.")
            except Exception as e:
                logger.error(f"Błąd ładowania pytań 1z10: {e}")
                self.questions = []
        else:
            self.questions = []

    def _save_questions(self) -> None:
        try:
            with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.questions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Błąd zapisu pytań 1z10: {e}")

    # --- Grupa Slash Commands /1z10 ---

    one_group = app_commands.Group(
        name="1z10",
        description="Teleturniej Jeden z dziesięciu (1 z 10) na kanale głosowym lub Scenie."
    )

    questions_group = app_commands.Group(
        name="pytania",
        description="Zarządzanie bazą pytań do teleturnieju 1 z 10.",
        parent=one_group
    )

    @one_group.command(name="start", description="Rozpoczyna teleturniej 1 z 10 (Lobby z zapisami na stanowiska).")
    @app_commands.describe(
        kanal="Wskaż kanał głosowy lub Scenę (Stage Channel), na którym odbędzie się teleturniej"
    )
    async def cmd_start(
        self,
        interaction: discord.Interaction,
        kanal: Optional[discord.VoiceChannel | discord.StageChannel] = None
    ) -> None:
        guild = interaction.guild
        member = interaction.user

        if not guild or not isinstance(member, discord.Member):
            await interaction.response.send_message("Komenda może być używana tylko na serwerze.", ephemeral=True)
            return

        if guild.id in self.games:
            await interaction.response.send_message(
                embed=error_embed("Gra w toku", "Na tym serwerze trwa już teleturniej 1 z 10! Zakończ go wpisując `/1z10 stop`."),
                ephemeral=True
            )
            return

        # Ustal kanał głosowy/scenę
        target_vc = kanal
        if not target_vc:
            if member.voice and member.voice.channel:
                target_vc = member.voice.channel
            else:
                await interaction.response.send_message(
                    embed=error_embed("Brak kanału", "Dołącz najpierw do kanału głosowego/Sceny lub wskaż go w opcji `kanal`."),
                    ephemeral=True
                )
                return

        await interaction.response.defer()

        # Połącz bota z kanałem głosowym / sceną
        try:
            vc = guild.voice_client
            if vc:
                if vc.channel.id != target_vc.id:
                    await vc.move_to(target_vc)
            else:
                vc = await target_vc.connect()

            # Jeśli to Stage Channel:
            if isinstance(target_vc, discord.StageChannel):
                try:
                    # Jeśli nie ma aktywnej instancji sceny, stwórz ją
                    if target_vc.instance is None:
                        await target_vc.create_stage_instance(topic="🎙️ Teleturniej 1 z 10 - Turniej Serwerowy")
                except Exception as e:
                    logger.warning(f"Błąd tworzenia instancji sceny: {e}")

                try:
                    # Bot staje się Speakerem na Scenie
                    await guild.me.edit(suppress=False)
                except Exception as e:
                    logger.warning(f"Błąd odciszania bota na Scenie: {e}")

        except Exception as e:
            await interaction.followup.send(
                embed=error_embed("Błąd połączenia", f"Nie udało się połączyć z kanałem głosowym: {e}"),
                ephemeral=True
            )
            return

        game = OneOutOfTenGame(
            cog=self,
            guild=guild,
            channel=interaction.channel,  # type: ignore
            voice_channel=target_vc,
            host=member
        )
        self.games[guild.id] = game

        embed = game.build_public_embed()
        view = RegistrationView(game)
        msg = await interaction.channel.send(embed=embed, view=view)
        game.public_message = msg

        # Odtwórz dżingiel powitalny
        game.play_sound("intro")

        await interaction.followup.send(
            embed=success_embed(
                "Lobby Utworzone",
                f"Teleturniej **1 z 10** został zainicjalizowany na {target_vc.mention}!\n"
                f"Prowadzący: {member.mention}\n"
                f"Uczestnicy mogą teraz zajmować stanowiska klikając przycisk poniżej."
            ),
            ephemeral=True
        )

    @one_group.command(name="panel", description="Otwiera panel sędziowski Prowadzącego (Tadeusza Sznuka).")
    async def cmd_panel(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild or guild.id not in self.games:
            await interaction.response.send_message(
                embed=error_embed("Brak gry", "Brak aktywnego teleturnieju na tym serwerze."),
                ephemeral=True
            )
            return

        game = self.games[guild.id]
        if interaction.user.id != game.host.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko Prowadzący ma dostęp do panelu sędziowskiego!", ephemeral=True)
            return

        embed = game.build_host_embed()
        view = HostControlView(game)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @one_group.command(name="stan", description="Wyświetla aktualny stan i tablicę wyników teleturnieju.")
    async def cmd_stan(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild or guild.id not in self.games:
            await interaction.response.send_message(
                embed=info_embed("Brak gry", "Na serwerze nie trwa żaden teleturniej 1 z 10. Uruchom go komendą `/1z10 start`."),
                ephemeral=True
            )
            return

        game = self.games[guild.id]
        await interaction.response.send_message(embed=game.build_public_embed(), ephemeral=True)

    @one_group.command(name="stop", description="Przerywa teleturniej 1 z 10 i rozłącza bota z kanału głosowego.")
    async def cmd_stop(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild or guild.id not in self.games:
            await interaction.response.send_message(embed=error_embed("Brak gry", "Brak gry do zatrzymania."), ephemeral=True)
            return

        game = self.games[guild.id]
        if interaction.user.id != game.host.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko Prowadzący lub Administrator może zatrzymać turniej.", ephemeral=True)
            return

        await game.stop()
        await interaction.response.send_message(embed=success_embed("Zatrzymano", "Teleturniej 1 z 10 został zakończony."))

    @one_group.command(name="dzwiek", description="Odtwarza testowy efekt dźwiękowy 1z10 na połączonym kanale głosowym.")
    @app_commands.describe(efekt="Wybierz efekt do odtworzenia")
    @app_commands.choices(efekt=[
        app_commands.Choice(name="Intro / Fanfary (intro)", value="intro"),
        app_commands.Choice(name="Buzzer (zgłoszenie)", value="buzzer"),
        app_commands.Choice(name="Poprawna odpowiedź (correct)", value="correct"),
        app_commands.Choice(name="Błędna odpowiedź (wrong)", value="wrong"),
        app_commands.Choice(name="Gong czasu (gong)", value="gong")
    ])
    async def cmd_sound(self, interaction: discord.Interaction, efekt: app_commands.Choice[str]) -> None:
        guild = interaction.guild
        if not guild:
            return

        vc = guild.voice_client
        if not vc or not vc.is_connected():
            await interaction.response.send_message("Bot nie jest połączony z żadnym kanałem głosowym.", ephemeral=True)
            return

        file_path = None
        for ext in (".mp3", ".wav"):
            cand = SOUNDS_DIR / f"{efekt.value}{ext}"
            if cand.exists():
                file_path = cand
                break

        if not file_path:
            await interaction.response.send_message(f"Brak pliku dźwiękowego `{efekt.value}` w `data/sounds_1z10/`.", ephemeral=True)
            return

        if vc.is_playing():
            vc.stop()

        ffmpeg_bin = "C:\\FFMPEG\\bin\\ffmpeg.exe" if Path("C:\\FFMPEG\\bin\\ffmpeg.exe").exists() else "ffmpeg"
        source = discord.FFmpegPCMAudio(str(file_path), executable=ffmpeg_bin)
        vc.play(source)

        await interaction.response.send_message(f"🔊 Odtwarzanie efektu `{efekt.name}` na kanale głosowym.", ephemeral=True)

    # --- Zarządzanie Pytaniami ---

    @questions_group.command(name="dodaj", description="Dodaje nowe pytanie do bazy teleturnieju 1 z 10.")
    @app_commands.describe(
        kategoria="Kategoria pytania (np. Historia, Geografia, Nauka, Popkultura, Sport)",
        pytanie="Treść pytania",
        odpowiedz="Prawidłowa odpowiedź"
    )
    async def cmd_questions_add(
        self,
        interaction: discord.Interaction,
        kategoria: str,
        pytanie: str,
        odpowiedz: str
    ) -> None:
        new_id = max([q.get("id", 0) for q in self.questions], default=0) + 1
        new_item = {
            "id": new_id,
            "category": kategoria.strip(),
            "question": pytanie.strip(),
            "answer": odpowiedz.strip()
        }
        self.questions.append(new_item)
        self._save_questions()

        embed = success_embed(
            "Dodano Pytanie",
            f"**ID:** #{new_id}\n"
            f"**Kategoria:** {new_item['category']}\n"
            f"**Pytanie:** {new_item['question']}\n"
            f"**Odpowiedź:** {new_item['answer']}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @questions_group.command(name="usun", description="Usuwa pytanie z bazy na podstawie identyfikatora ID.")
    @app_commands.describe(id="ID pytania do usunięcia")
    async def cmd_questions_delete(self, interaction: discord.Interaction, id: int) -> None:
        idx = next((i for i, q in enumerate(self.questions) if q.get("id") == id), None)
        if idx is None:
            await interaction.response.send_message(embed=error_embed("Błąd", f"Nie znaleziono pytania o ID #{id}."), ephemeral=True)
            return

        removed = self.questions.pop(idx)
        self._save_questions()
        await interaction.response.send_message(
            embed=success_embed("Usunięto Pytanie", f"Pytanie #{id} (\"{removed.get('question')}\") zostało usunięte."),
            ephemeral=True
        )

    @questions_group.command(name="lista", description="Wyświetla listę pytań z bazy.")
    @app_commands.describe(kategoria="Filtruj według kategorii", strona="Numer strony (po 10 pytań)")
    async def cmd_questions_list(
        self,
        interaction: discord.Interaction,
        kategoria: Optional[str] = None,
        strona: int = 1
    ) -> None:
        filtered = self.questions
        if kategoria:
            filtered = [q for q in self.questions if kategoria.lower() in q.get("category", "").lower()]

        if not filtered:
            await interaction.response.send_message("Brak pytań spełniających kryteria.", ephemeral=True)
            return

        page_size = 8
        total_pages = (len(filtered) + page_size - 1) // page_size
        strona = max(1, min(strona, total_pages))

        start = (strona - 1) * page_size
        items = filtered[start:start + page_size]

        lines = []
        for q in items:
            lines.append(f"**#{q.get('id')} [{q.get('category')}]** {q.get('question')}\n💡 *Odp: {q.get('answer')}*\n")

        embed = create_embed(
            title=f"📚 Baza Pytań 1 z 10 (Strona {strona}/{total_pages})",
            description="\n".join(lines),
            color=config.INFO_COLOR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @questions_group.command(name="statystyki", description="Wyświetla statystyki bazy pytań.")
    async def cmd_questions_stats(self, interaction: discord.Interaction) -> None:
        total = len(self.questions)
        categories: dict[str, int] = {}
        for q in self.questions:
            c = q.get("category", "Inne")
            categories[c] = categories.get(c, 0) + 1

        cat_lines = [f"• **{cat}**: {count} pytań" for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True)]

        embed = create_embed(
            title="📊 Statystyki Bazy Pytań 1 z 10",
            description=f"Łączna liczba pytań w bazie: **{total}**\n\n**Rozkład kategorii:**\n" + "\n".join(cat_lines),
            color=config.DEFAULT_EMBED_COLOR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OneOutOfTenCog(bot))
