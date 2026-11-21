"""Optional local-AI assistant (Hour 5). Off by default; never trusted; never confirms anything.

    from ai import AssistedInterpreter
    from dialogue import DialogueManager
    mgr = DialogueManager(interpreter=AssistedInterpreter())   # enable with VAADA_AI_ENABLED=1
"""
from .config import AIConfig
from .interpreter import AssistedInterpreter, Interpretation

__all__ = ["AIConfig", "AssistedInterpreter", "Interpretation"]
