"""What Hour 3 needs to know about the conversation: just a tiny description of
what (if anything) is pending. Hour 4 will own the real conversation state and
create these descriptors; Hour 3 only READS one, never stores or changes it."""
from dataclasses import dataclass
from typing import Optional

AWAITING_CONFIRMATION = "CONFIRMATION"   # we asked "Ramesh ke Rs 300 add kar doon?"
AWAITING_CLARIFICATION = "CLARIFICATION"  # we asked "Auto wale ya school wale Ramesh?"
_AWAITING = {AWAITING_CONFIRMATION, AWAITING_CLARIFICATION}
# only these intents can be the thing awaiting confirmation (they are the "write proposals")
_PROPOSAL_INTENTS = {"ADD_CREDIT", "RECORD_PAYMENT", "SET_COMMITMENT"}


@dataclass(frozen=True)
class PendingContext:
    awaiting: str                          # AWAITING_CONFIRMATION or AWAITING_CLARIFICATION
    action_intent: Optional[str] = None    # intent of the pending proposal, if known
    has_customer: bool = False             # a current customer exists for "usne ..." follow-ups

    def __post_init__(self):
        if self.awaiting not in _AWAITING:
            raise ValueError(f"awaiting must be one of {sorted(_AWAITING)}, got {self.awaiting!r}")
        if self.action_intent is not None and self.action_intent not in _PROPOSAL_INTENTS:
            raise ValueError(f"action_intent must be one of {sorted(_PROPOSAL_INTENTS)} or None")
