"""
Trwałe widoki oraz formularze modalne weryfikacji nowych użytkowników.
"""

import io
import asyncio
import random
import logging
import discord

from utils.embeds import success_embed, error_embed
from utils.card_generator import create_captcha_image

logger = logging.getLogger("AegisBot.VerificationViews")

class MathVerificationModal(discord.ui.Modal, title="Weryfikacja — Działanie Matematyczne"):
    def __init__(self, num1: int, num2: int) -> None:
        super().__init__()
        self.num1 = num1
        self.num2 = num2
        self.correct_answer = num1 + num2

        self.answer_input = discord.ui.TextInput(
            label=f"Ile to jest {num1} + {num2}?",
            placeholder="Wpisz wynik liczbowy...",
            max_length=10,
            required=True
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            user_ans = int(self.answer_input.value.strip())
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Błąd", "Wprowadzono niepoprawną liczbę."), ephemeral=True)
            return

        if user_ans == self.correct_answer:
            await complete_verification(interaction)
        else:
            await interaction.response.send_message(embed=error_embed("Weryfikacja Nieudana", "Niepoprawny wynik działania matematycznego."), ephemeral=True)


class CodeVerificationModal(discord.ui.Modal, title="Weryfikacja — Kod CAPTCHA"):
    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = code

        self.code_input = discord.ui.TextInput(
            label=f"Przepisz kod z obrazka / tekstu",
            placeholder="Wpisz dokładnie kod CAPTCHA...",
            max_length=20,
            required=True
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.code_input.value.strip().upper() == self.code:
            await complete_verification(interaction)
        else:
            await interaction.response.send_message(embed=error_embed("Weryfikacja Nieudana", "Niepoprawnie przepisany kod CAPTCHA."), ephemeral=True)


class VerificationButtonView(discord.ui.View):
    """Trwały przycisk weryfikacji wyświetlany na kanale powitalnym/weryfikacji."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Zweryfikuj się", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify_user_button")
    async def verify_click(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        db = interaction.client.db
        guild = interaction.guild
        member = interaction.user

        if not guild or not isinstance(member, discord.Member):
            return

        cfg = await db.fetchone("SELECT verified_role_id, unverified_role_id, verification_mode FROM guild_config WHERE guild_id = ?", (guild.id,))
        if not cfg or not cfg["verified_role_id"]:
            await interaction.response.send_message(embed=error_embed("Błąd Konfiguracji", "Rola weryfikacji nie została skonfigurowana przez administratora."), ephemeral=True)
            return

        verified_role = guild.get_role(cfg["verified_role_id"])
        if verified_role and verified_role in member.roles:
            await interaction.response.send_message(embed=error_embed("Błąd", "Jesteś już zweryfikowany na tym serwerze!"), ephemeral=True)
            return

        mode = cfg["verification_mode"] or "button"

        if mode == "button":
            await complete_verification(interaction)
        elif mode == "math":
            n1 = random.randint(1, 20)
            n2 = random.randint(1, 20)
            await interaction.response.send_modal(MathVerificationModal(n1, n2))
        elif mode == "code" or mode == "captcha":
            code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=5))
            captcha_buf = await asyncio.to_thread(create_captcha_image, code)
            file = discord.File(captcha_buf, filename="captcha.png")

            # Powiadom z załączonym kodem/obrazkiem
            modal = CodeVerificationModal(code)
            await interaction.response.send_modal(modal)


async def complete_verification(interaction: discord.Interaction) -> None:
    """Nadaje rolę zweryfikowanego użytkownika i usuwa ewentualną rolę niezweryfikowanego."""
    guild = interaction.guild
    member = interaction.user
    db = interaction.client.db

    if not guild or not isinstance(member, discord.Member):
        return

    cfg = await db.fetchone("SELECT verified_role_id, unverified_role_id FROM guild_config WHERE guild_id = ?", (guild.id,))
    if not cfg or not cfg["verified_role_id"]:
        return

    verified_role = guild.get_role(cfg["verified_role_id"])
    unverified_role = guild.get_role(cfg["unverified_role_id"]) if cfg["unverified_role_id"] else None

    try:
        if verified_role:
            await member.add_roles(verified_role, reason="Pomyślna weryfikacja")
        if unverified_role and unverified_role in member.roles:
            await member.remove_roles(unverified_role, reason="Pomyślna weryfikacja")

        msg = success_embed("Weryfikacja Pomyślna", "Zostałeś pomyślnie zweryfikowany na serwerze! Uzyskałeś pełny dostęp do kanałów.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)
    except discord.HTTPException as e:
        err = error_embed("Błąd Uprawnień Bota", f"Nie udało się nadać roli: {e}")
        if interaction.response.is_done():
            await interaction.followup.send(embed=err, ephemeral=True)
        else:
            await interaction.response.send_message(embed=err, ephemeral=True)
