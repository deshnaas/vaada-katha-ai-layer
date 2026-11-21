"""Finding the customer NAME in an utterance. This never chooses between
customers: if several could match, they are all returned as candidates.

Two modes:
  * known_customers given (Person 3 will pass the real list): match against it.
  * no list: guess the name from sentence shape ("<name> ka/ne/ko/se ...").
"""
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Optional

from . import lexicon as L

_STOP = (set(L.FILLERS) | L.PARTICLES | L.TITLES | L.WALE | L.UNIT_WORDS | L.CURRENCY_WORDS
         | set(L.WEEKDAYS) | L.MONTHS | L.METHOD_WORDS | L.CREDIT_STRONG | L.PAYMENT_STRONG
         | L.PAYMENT_MED | L.DIYE | L.QUESTION_WORDS | L.BALANCE_WORDS | L.HISTORY_WORDS
         | L.FUTURE_VERBS | L.CORR_START | L.CONFIRM_TOKENS | L.PRONOUNS | L.CORR_LEAD_FILLERS
         | set(L.NUMBER_WORDS) | set(L.MULT) | set(L.FRAC_PREFIX) | L.SAADE | L.PAUNE
         | {"hey", "vaada"})


@dataclass
class CustomerInfo:
    name: Optional[str] = None
    qualifier: Optional[str] = None
    candidates: List[dict] = field(default_factory=list)
    refers_to_context: bool = False
    ambiguities: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _is_word(tok: str) -> bool:
    return not any(ch.isdigit() for ch in tok) and tok.replace("-", "").replace("'", "").isalpha() \
        or any("ऀ" <= ch <= "ॿ" for ch in tok)


def _name_ok(tok: str) -> bool:
    return bool(tok) and tok not in _STOP and _is_word(tok)


def _pretty(tok_o: str) -> str:
    return tok_o if tok_o[:1].isupper() or any("ऀ" <= c <= "ॿ" for c in tok_o) \
        else tok_o.capitalize()


def _qualifier_around(toks_o, toks, k) -> Optional[str]:
    """'auto wale Ramesh' or 'Ramesh auto wale' -> 'auto'."""
    if k >= 2 and toks[k - 1] in L.WALE and toks[k - 2] not in L.PARTICLES:
        return toks_o[k - 2].lower()
    if k + 2 < len(toks) and toks[k + 2] in L.WALE:
        return toks_o[k + 1].lower()
    return None


def _guess(toks_o: List[str], toks: List[str], strict: bool = False) -> CustomerInfo:
    info = CustomerInfo()
    n = len(toks)
    # 1) <name> + particle (ka/ki/ke/ne/ko/se)
    for i in range(1, n):
        if toks[i] in L.PARTICLES:
            j = i - 1
            if toks[j] in L.WALE and j >= 2:           # "Ramesh auto wale ka"
                info.qualifier = toks_o[j - 1].lower()
                j = j - 2
            while j >= 0 and toks[j] in L.TITLES:       # "Ramesh bhai ka"
                j -= 1
            if j >= 0 and _name_ok(toks[j]):
                name = _pretty(toks_o[j])
                # two capitalised words together = one name ("Ram Kumar")
                if j >= 1 and _name_ok(toks[j - 1]) and toks_o[j - 1][:1].isupper() \
                        and toks_o[j][:1].isupper():
                    name = _pretty(toks_o[j - 1]) + " " + name
                    j -= 1
                info.name = name
                info.qualifier = info.qualifier or _qualifier_around(toks_o, toks, j)
                return info
    # 2) English: "for/to/from <name>"
    for i in range(n - 1):
        if toks[i] in ("for", "to", "from") and _name_ok(toks[i + 1]):
            info.name = _pretty(toks_o[i + 1])
            return info
    # 3) exactly one unknown word left -> it is probably the name
    rest = [i for i in range(n) if _name_ok(toks[i])
            and not (i + 1 < n and toks[i + 1] in L.WALE)]   # "auto" in "auto wale" is a qualifier
    if len(rest) == 1 and not strict:     # strict (used for corrections) never guesses a bare word
        k = rest[0]
        info.name = _pretty(toks_o[k])
        info.qualifier = _qualifier_around(toks_o, toks, k)
        return info
    # 4) only a qualifier: "chai wale"
    for i in range(1, n):
        if toks[i] in L.WALE and toks[i - 1] not in L.PARTICLES and toks[i - 1] not in _STOP:
            info.qualifier = toks_o[i - 1].lower()
            return info
    if len(rest) > 1 and not strict:
        info.ambiguities.append("CUSTOMER_UNCLEAR")
    return info


def _match_known(toks_o, toks, known: List[dict], strict: bool = False) -> CustomerInfo:
    """customer_name is always the word the shopkeeper SAID. The list entries that could
    match it go in `candidates`; choosing between them is somebody else's job."""
    info = CustomerInfo()
    heard_orig = {}
    for o, t in zip(toks_o, toks):
        heard_orig.setdefault(t, o)
    utter = [t for t in toks if t not in L.PARTICLES]
    utter_words = set(utter)
    exact, fuzzy = [], []          # lists of (index_in_known, heard_token)
    for idx, c in enumerate(known):
        parts = [p.lower() for p in c["name"].split()]
        hit = next((t for t in utter if t in parts), None)
        if hit:
            exact.append((idx, hit))
            continue
        for t in utter:
            if len(t) >= 4 and t not in _STOP and \
                    SequenceMatcher(None, t, parts[0]).ratio() >= 0.8:
                fuzzy.append((idx, t))
                break
    alias_hits = []
    for idx, c in enumerate(known):
        for a in c.get("aliases", []):
            parts = {p.lower() for p in a.split() if p.lower() not in L.WALE}
            if parts & utter_words:
                alias_hits.append(idx)
                break

    def brief(i):
        c = known[i]
        return {"customer_id": c.get("id"), "name": c["name"], "aliases": c.get("aliases", [])}

    if exact:
        pool = exact
        if len(pool) > 1:
            narrowed = [p for p in pool if p[0] in alias_hits]
            if len(narrowed) == 1:                      # user said "auto wale Ramesh"
                pool = narrowed
        info.candidates = [brief(i) for i, _ in pool]
        info.name = _pretty(heard_orig[pool[0][1]])
        if len(pool) > 1:
            info.ambiguities.append("AMBIGUOUS_CUSTOMER")
    elif fuzzy:
        info.candidates = [brief(i) for i, _ in fuzzy]
        info.name = _pretty(heard_orig[fuzzy[0][1]])    # keep the misheard word visible
        info.ambiguities.append("CUSTOMER_FUZZY_MATCH")
    elif alias_hits:
        info.candidates = [brief(i) for i in alias_hits]
        info.name = None
        if len(alias_hits) > 1:
            info.ambiguities.append("AMBIGUOUS_CUSTOMER")
    else:
        # not in the list: fall back to shape-guessing so the caller can offer "new customer?"
        guess = _guess(toks_o, toks, strict)
        if guess.name:
            guess.warnings.append("CUSTOMER_NOT_IN_LIST")
        return guess
    info.qualifier = _qualifier_from_tokens(toks_o, toks)
    return info


def _qualifier_from_tokens(toks_o, toks) -> Optional[str]:
    for i in range(1, len(toks)):
        if toks[i] in L.WALE and toks[i - 1] not in L.PARTICLES:
            return toks_o[i - 1].lower()
    return None


_JOINERS = {"aur", "and", "ya", "or", "tatha", "aur"}


def _joined_names(toks_o: List[str], toks: List[str]) -> List[str]:
    """'Ramesh aur Suresh ka 300' -> ['Ramesh', 'Suresh'] (two customers named for one entry)."""
    for i in range(1, len(toks)):
        if toks[i] in L.PARTICLES:
            j = i - 1
            while j >= 0 and toks[j] in L.TITLES:
                j -= 1
            if j < 0 or not _name_ok(toks[j]):
                continue
            names, k = [toks_o[j]], j - 1
            while k >= 1 and toks[k] in _JOINERS and _name_ok(toks[k - 1]):
                names.insert(0, toks_o[k - 1])
                k -= 2
            if len(names) >= 2:
                return [_pretty(n) for n in names]
            return []
    return []


def _several_customers(names: List[str], known: Optional[List[dict]]) -> CustomerInfo:
    """Two or more customers were named: never pick one. List every possibility."""
    info = CustomerInfo()
    info.ambiguities.append("AMBIGUOUS_CUSTOMER")
    seen = set()
    for nm in names:
        found = []
        if known:
            found = _match_known([nm], [nm.lower()], known, strict=True).candidates
        if not found:
            found = [{"customer_id": None, "name": nm, "aliases": []}]
        for c in found:
            key = (c.get("customer_id"), c["name"].lower())
            if key not in seen:
                seen.add(key)
                info.candidates.append(c)
    return info


def find_customer(toks_o: List[str], toks: List[str],
                  known_customers: Optional[List[dict]] = None,
                  strict: bool = False) -> CustomerInfo:
    names = _joined_names(toks_o, toks)
    if len(names) >= 2:                                # "Ramesh aur Suresh ka ..." is never resolved here
        return _several_customers(names, known_customers)
    info = _match_known(toks_o, toks, known_customers, strict) if known_customers \
        else _guess(toks_o, toks, strict)
    if info.name is None and not info.candidates and info.qualifier is None \
            and any(t in L.PRONOUNS for t in toks):
        info.refers_to_context = True                   # "usne 200 diye" -> the current customer
        info.warnings.append("CUSTOMER_FROM_CONTEXT")
    return info
