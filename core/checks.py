"""
Sprawdziany i dekoratory uprawnień wykorzystywane w komendach slash.
"""

from typing import Callable, TypeVar
import discord
from discord import app_commands

T = TypeVar("T")

def is_owner_check(interaction: discord.Interaction) -> bool:
    """Sprawdza czy użytkownik wykonujący komendę jest właścicielem bota z pliku .env."""
    from config import OWNER_ID
    return interaction.user.id == OWNER_ID

def is_owner():
    """Dekorator sprawdzający czy użytkownik to właściciel bota."""
    return app_commands.check(is_owner_check)

def is_guild_owner():
    """Dekorator sprawdzający czy użytkownik jest właścicielem serwera Discord."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.owner_id != interaction.user.id:
            raise app_commands.CheckFailure("Ta komenda jest zastrzeżona wyłącznie dla Właściciela Serwera.")
        return True
    return app_commands.check(predicate)

def is_administrator():
    """Dekorator sprawdzający uprawnienia Administratora serwera."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id:
            return True
        raise app_commands.CheckFailure("Ta komenda wymaga uprawnień Administratora na serwerze.")
    return app_commands.check(predicate)
