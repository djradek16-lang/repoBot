"""
Definicje stałych systemowych i typów wyliczeniowych dla bota Discord.
"""

from enum import Enum

class ModerationAction(str, Enum):
    BAN = "BAN"
    SOFTBAN = "SOFTBAN"
    UNBAN = "UNBAN"
    KICK = "KICK"
    TIMEOUT = "TIMEOUT"
    UNTIMEOUT = "UNTIMEOUT"
    WARN = "WARN"
    UNWARN = "UNWARN"
    MUTE = "MUTE"
    UNMUTE = "UNMUTE"

class TicketStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"

class SuggestionStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    IMPLEMENTED = "IMPLEMENTED"
    IN_PROGRESS = "IN_PROGRESS"

class VerificationMode(str, Enum):
    BUTTON = "button"
    MATH = "math"
    CODE = "code"
    CAPTCHA = "captcha"
