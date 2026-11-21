"""Relative-date understanding: aaj, kal, parso, Friday, 15 tarikh, 3 din baad.

Important Hindi quirk: "kal" means BOTH yesterday and tomorrow, and "parso" both
the day before yesterday and the day after tomorrow. We decide using the tense of
the sentence ("diya tha" = past, "dega" = future). If the tense does not tell us,
we do NOT guess: date_resolved stays None and the caller flags it.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional
import calendar

from . import lexicon as L
from .amounts import word_to_int

PAST, FUTURE = "PAST", "FUTURE"


@dataclass
class DateInfo:
    expression: Optional[str] = None     # exactly what was said, e.g. "kal"
    resolved: Optional[str] = None       # ISO date, or None if unknown
    direction: Optional[str] = None      # PAST / FUTURE / PRESENT / None
    ambiguous: bool = False              # said a date word but could not resolve it


def detect_tense(tokens: List[str]) -> Optional[str]:
    past = any(t in L.PAST_CUES for t in tokens)
    future = any(t in L.FUTURE_CUES for t in tokens)
    if past and not future:
        return PAST
    if future and not past:
        return FUTURE
    return None


def _shift(today: date, days: int) -> str:
    return (today + timedelta(days=days)).isoformat()


def _weekday(today: date, target: int, tense: str) -> date:
    if tense == FUTURE:
        delta = (target - today.weekday()) % 7 or 7       # next one, never "today"
        return today + timedelta(days=delta)
    delta = (today.weekday() - target) % 7 or 7           # previous one
    return today - timedelta(days=delta)


def _day_of_month(today: date, dom: int, tense: str) -> Optional[date]:
    y, m = today.year, today.month
    for _ in range(14):
        last = calendar.monthrange(y, m)[1]
        if dom <= last:
            cand = date(y, m, dom)
            if tense == FUTURE and cand > today:
                return cand
            if tense == PAST and cand < today:
                return cand
        if tense == FUTURE:
            m += 1
            if m == 13:
                m, y = 1, y + 1
        else:
            m -= 1
            if m == 0:
                m, y = 12, y - 1
    return None


def parse_date(tokens: List[str], today: date, force: Optional[str] = None) -> DateInfo:
    """force = PAST/FUTURE when the caller already knows (e.g. promises are future)."""
    tense = force or detect_tense(tokens)
    n = len(tokens)
    for i, t in enumerate(tokens):
        if t in L.DAYS_ABOUT_TODAY:
            return DateInfo(t, today.isoformat(), "PRESENT")
        if t == "tomorrow":
            return DateInfo(t, _shift(today, 1), FUTURE)
        if t == "yesterday":
            return DateInfo(t, _shift(today, -1), PAST)
        if t in ("kal", "कल", "parso", "parson", "परसों", "parsoo"):
            mag = 1 if t in ("kal", "कल") else 2
            if tense == PAST:
                return DateInfo(t, _shift(today, -mag), PAST)
            if tense == FUTURE:
                return DateInfo(t, _shift(today, mag), FUTURE)
            return DateInfo(t, None, None, ambiguous=True)
        if t in L.WEEKDAYS:
            if tense is None:
                return DateInfo(t, None, None, ambiguous=True)
            return DateInfo(t, _weekday(today, L.WEEKDAYS[t], tense).isoformat(), tense)
        num = word_to_int(t)
        if num is not None and i + 1 < n:
            nxt = tokens[i + 1]
            if nxt in ("tarikh", "tareekh", "date", "तारीख") and 1 <= num <= 31:
                if tense is None:
                    return DateInfo(f"{t} {nxt}", None, None, ambiguous=True)
                d = _day_of_month(today, num, tense)
                return DateInfo(f"{t} {nxt}", d.isoformat() if d else None, tense, ambiguous=d is None)
            if nxt in ("din", "days", "day") and i + 2 < n:
                after = tokens[i + 2]
                if after in ("baad", "mein", "me", "later"):
                    return DateInfo(f"{t} {nxt} {after}", _shift(today, num), FUTURE)
                if after in ("pehle", "ago"):
                    return DateInfo(f"{t} {nxt} {after}", _shift(today, -num), PAST)
    return DateInfo()
