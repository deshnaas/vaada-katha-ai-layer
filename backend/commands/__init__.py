"""Vaada Khata command parsing (Hour 3): IntentResult -> validated Command.

    from commands import parse_command, PendingContext
    cmd = parse_command(intent_result)               # no conversation context
    cmd.status, cmd.payload, cmd.is_complete
"""
from .context import AWAITING_CLARIFICATION, AWAITING_CONFIRMATION, PendingContext
from .models import Command
from .parser import command_from_text, customer_ref_from, parse_command

__all__ = ["parse_command", "command_from_text", "customer_ref_from", "Command", "PendingContext",
           "AWAITING_CONFIRMATION", "AWAITING_CLARIFICATION"]
