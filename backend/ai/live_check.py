"""MANUAL smoke test against your real local Ollama model. Not part of the pytest suite.

Run from backend/:   python -m ai.live_check            (uses VAADA_AI_MODEL or qwen3:4b)
                     python -m ai.live_check qwen3:1.7b
It makes a handful of local requests (cost: zero; no Gnani credits, no internet) and prints what
the assistant did with each sentence and how long the model took.
"""
import sys
from datetime import date

from .config import AIConfig
from .client import OllamaClient
from .interpreter import AssistedInterpreter

SENTENCES = [
    "Ramesh ke naam pe teen sau chadha do",         # unknown verb: should be rescued as credit
    "Lakshmi akka ne paanch sau thama",             # unknown verb: payment
    "put 250 on Suresh's tab",                      # English phrasing
    "Suresh Friday ko paanch sau bharega",          # promise with an unknown verb
    "Ravi ke saath kya chal raha hai",              # open question
    "Ramesh ka 300 likho",                          # genuinely ambiguous: must NOT be guessed
    "Ramesh ka 300 udhaar likho",                   # rules already understand: model not used
    "mausam kaisa hai",                             # unrelated: model not used
]


def main() -> None:
    cfg = AIConfig.from_env()
    if len(sys.argv) > 1:
        cfg = AIConfig(enabled=True, model=sys.argv[1], base_url=cfg.base_url, timeout_s=max(cfg.timeout_s, 60))
    else:
        cfg = AIConfig(enabled=True, model=cfg.model, base_url=cfg.base_url, timeout_s=max(cfg.timeout_s, 60))
    print(f"model={cfg.model}  url={cfg.base_url}  (timeout {cfg.timeout_s:.0f}s for this check)")
    print("warming up (the first request loads the model and can be slow)...")
    ok = OllamaClient(cfg).warm_up(120)
    print("model reachable:", ok)
    interp = AssistedInterpreter(cfg)
    today = date.today()
    for text in SENTENCES:
        out = interp.interpret(text, today=today)
        r = out.result
        ms = f"{out.ai_ms:.0f} ms" if out.ai_ms is not None else "-"
        print(f"\n{text!r}\n   source={out.source} status={out.status} {out.reason}  [{ms}]")
        print(f"   -> intent={r.intent} customer={r.customer_name} amount_paise={r.amount_paise} "
              f"type={r.transaction_type} confirm_needed={r.requires_confirmation} ambiguities={r.ambiguities}")
        if out.canonical_text:
            print(f"   canonical: {out.canonical_text!r}")


if __name__ == "__main__":
    main()
