"""Vaada Khata confirmation layer (Hour 4).

    from dialogue import DialogueManager
    mgr = DialogueManager()
    r = mgr.process_text("shop1", "Ramesh ka 300 udhaar likho")   # r.message = the prompt to speak
    r = mgr.process_text("shop1", "haan")                         # r.execution_request = hand-off
"""
from .manager import DialogueManager
from .models import ExecutionRequest, PendingSnapshot, TurnResult

__all__ = ["DialogueManager", "TurnResult", "ExecutionRequest", "PendingSnapshot"]
