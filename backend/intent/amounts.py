"""Deterministic amount parsing: digits, Hindi words, English words, mixes.

Examples that must work (all become integer paise):
  "340", "₹340", "1,200", "teen sau chaalis" (340), "three-forty" (340),
  "do hazaar paanch sau" (2500), "dedh sau" (150), "saade teen sau" (350),
  "2 hazaar", "1 lakh", "340.50"

No floats are used for money: values are Decimal, then converted to integer paise.
"""
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple

from . import lexicon as L

_DIGIT_RE = re.compile(r"^\d+(\.\d+)?$")
_EN_TENS = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"}


@dataclass
class AmountCandidate:
    paise: int
    start: int          # token index where the amount begins
    end: int            # token index just after it
    text: str
    has_currency: bool  # "rupaye"/"rs" right next to it
    shorthand: bool = False   # "three forty" style
    rounded: bool = False


def word_to_int(tok: str) -> Optional[int]:
    """Single token -> small int (digits or number word). Used by the date parser too."""
    if _DIGIT_RE.match(tok) and "." not in tok:
        return int(tok)
    if tok in L.NUMBER_WORDS:
        return L.NUMBER_WORDS[tok]
    return None


def _single_value(tok: str) -> Optional[Decimal]:
    if _DIGIT_RE.match(tok):
        return Decimal(tok)
    if tok in L.NUMBER_WORDS:
        return Decimal(L.NUMBER_WORDS[tok])
    return None


# An "item" is (kind, value, is_digit, start, end)
#   NUM  : a number token          MULT : sau / hazaar / lakh
#   FRAC : already-complete value ("dedh sau" = 150)     AND : English "and"
def _items(tokens: List[str]) -> list:
    out = []
    n = len(tokens)
    i = 0
    while i < n:
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < n else ""
        if t in L.FRAC_PREFIX and nxt in L.MULT:
            out.append(("FRAC", Decimal(str(L.FRAC_PREFIX[t])) * L.MULT[nxt], False, i, i + 2))
            i += 2
            continue
        if (t in L.SAADE or t in L.PAUNE) and i + 2 < n and tokens[i + 2] in L.MULT:
            base = _single_value(nxt)
            if base is not None:
                delta = Decimal("0.5") if t in L.SAADE else Decimal("-0.25")
                out.append(("FRAC", (base + delta) * L.MULT[tokens[i + 2]], False, i, i + 3))
                i += 3
                continue
        if _DIGIT_RE.match(t):
            out.append(("NUM", Decimal(t), True, i, i + 1))
        elif t in L.MULT:
            out.append(("MULT", L.MULT[t], False, i, i + 1))
        elif t in L.NUMBER_WORDS:
            tens_follows = L.NUMBER_WORDS.get(nxt, 0) >= 20 and L.NUMBER_WORDS.get(nxt, 0) % 10 == 0
            prv = tokens[i - 1] if i > 0 else ""
            one_continues = (t == "one" and (
                tens_follows                                    # "one fifty" = 150
                or prv in _EN_TENS or prv in ("hundred", "thousand", "and")))  # "twenty one", "hundred one"
            if t in L.NUMBER_HOMOGRAPHS and not (
                    nxt in L.MULT or nxt in L.CURRENCY_WORDS or one_continues):
                i += 1
                continue
            out.append(("NUM", Decimal(L.NUMBER_WORDS[t]), False, i, i + 1))
        elif t == "and":
            out.append(("AND", None, False, i, i + 1))
        i += 1
    return out


def _can_follow(prev: tuple, cur: tuple) -> bool:
    if prev[4] != cur[3]:           # must be adjacent tokens
        return False
    pk, ck = prev[0], cur[0]
    if ck == "AND":
        return pk == "MULT"
    if pk == "AND":
        return ck == "NUM"
    if ck == "MULT":
        return pk == "NUM"
    if ck == "NUM":
        if pk == "MULT":
            return cur[1] < prev[1]               # "teen sau CHAALIS" yes, "5 sau 200" no
        if pk == "NUM" and not prev[2] and not cur[2]:   # both words
            pv, cv = prev[1], cur[1]
            if cv >= 20 and cv % 10 == 0 and 1 <= pv <= 9:   # "three forty"
                return True
            if pv >= 20 and pv % 10 == 0 and 1 <= cv <= 9:   # "forty five"
                return True
    return False


def _evaluate(span: list) -> Tuple[Decimal, bool]:
    total = Decimal(0)
    current = Decimal(0)
    shorthand = False
    prev = None
    for it in span:
        kind = it[0]
        if kind == "FRAC":
            current += it[1]
        elif kind == "NUM":
            v = it[1]
            if (prev and prev[0] == "NUM" and not prev[2] and not it[2]
                    and v >= 20 and v % 10 == 0 and 1 <= prev[1] <= 9):
                current = prev[1] * 100 + v      # "three forty" -> 340
                shorthand = True
            else:
                current += v
        elif kind == "MULT":
            scale = Decimal(it[1])
            if scale == 100:
                current = (current or Decimal(1)) * 100
            else:
                total += (current or Decimal(1)) * scale
                current = Decimal(0)
        prev = it
    return total + current, shorthand


def _split_repeated_hundreds(span: list) -> List[list]:
    """A real number has at most one 'sau' per thousand-group. "teen sau paanch sau" is two
    amounts spoken back to back (300, 500), not 30,500: split so the user is asked."""
    parts: List[list] = []
    cur: list = []
    last_sau = -1                        # index in cur of the latest 'sau' of the current group
    for it in span:
        if it[0] == "MULT" and it[1] == 100 and last_sau >= 0:
            head, tail = cur[:last_sau + 1], cur[last_sau + 1:]
            if head:
                parts.append(head)
            cur = tail + [it]
            last_sau = len(cur) - 1
            continue
        cur.append(it)
        if it[0] == "MULT":
            last_sau = len(cur) - 1 if it[1] == 100 else -1     # hazaar/lakh starts a new group
    if cur:
        parts.append(cur)
    return parts


def find_amounts(tokens: List[str]) -> List[AmountCandidate]:
    items = _items(tokens)
    spans: List[list] = []
    cur: list = []
    for it in items:
        if cur and _can_follow(cur[-1], it):
            cur.append(it)
        else:
            if cur:
                spans.append(cur)
            cur = [it]
    if cur:
        spans.append(cur)

    spans = [part for s in spans for part in _split_repeated_hundreds(s)]

    out: List[AmountCandidate] = []
    for span in spans:
        while span and span[-1][0] == "AND":
            span = span[:-1]
        while span and span[0][0] == "AND":
            span = span[1:]
        if not span:
            continue
        start, end = span[0][3], span[-1][4]
        nxt = tokens[end] if end < len(tokens) else ""
        prv = tokens[start - 1] if start > 0 else ""
        if nxt in L.UNIT_WORDS or nxt in L.TITLES:
            continue                                    # "2 kilo", "15 tarikh", "Das ji"
        if len(span) == 1 and span[0][0] == "NUM" and span[0][2] and len(tokens[start]) >= 8 \
                and "." not in tokens[start]:
            continue                                    # phone-number-like
        value, shorthand = _evaluate(span)
        paise_exact = value * 100
        paise = int(paise_exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        out.append(AmountCandidate(
            paise=paise, start=start, end=end, text=" ".join(tokens[start:end]),
            has_currency=(nxt in L.CURRENCY_WORDS or prv in L.CURRENCY_WORDS),
            shorthand=shorthand, rounded=(paise_exact != paise),
        ))
    return out


def select_amount(cands: List[AmountCandidate]) -> Tuple[Optional[AmountCandidate], bool]:
    """Pick THE amount, or report that it is ambiguous. Returns (candidate, ambiguous)."""
    if not cands:
        return None, False
    if len(cands) == 1:
        return cands[0], False
    marked = [c for c in cands if c.has_currency]
    if len(marked) == 1:
        return marked[0], False
    return None, True
