"""Find entities by name without embeddings: whole-name phrases first, then shared name words."""

import re
from typing import Any

from app.extraction.schema import slug

STOPWORDS = frozenset(
    """a about after all also an and any are as at be because been before but by can did do does
    for from had has have he her him his how i if in into is it its me my no not of on or our she
    so than that the their them then there these they this to us was we were what when where which
    who whom whose why will with would you your""".split()
)
MIN_WORD_LEN = 3
PARTIAL_WEIGHT = 0.8  # a name matched only by some of its words scores at most this
ALIAS_WEIGHT = 1.0
MIN_SCORE = 0.35


def words(text: str) -> list[str]:
    """Lowercase ASCII words: "Zoë's cat" -> ['zoe', 's', 'cat']."""
    return [w for w in slug(text).split("_") if w]


def content_words(text: str) -> list[str]:
    return [w for w in words(text) if len(w) >= MIN_WORD_LEN and w not in STOPWORDS]


def _contains_phrase(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def _score(text_words: list[str], name: str, weight: float) -> float:
    name_words = words(name)
    if _contains_phrase(text_words, name_words):
        return weight
    significant = [w for w in name_words if len(w) >= MIN_WORD_LEN and w not in STOPWORDS]
    if not significant:
        return 0.0
    hit = sum(1 for w in significant if w in text_words)
    return PARTIAL_WEIGHT * weight * hit / len(significant)


def match_entities(text: str, index: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Entities whose name or alias appears in `text`, best first (ties: most mentioned).

    Each result is an index row plus `score` and `matched` (the name or alias that matched).
    """
    text_words = words(text)
    found = []
    for e in index:
        best, matched = 0.0, ""
        for name, weight in [(e["name"], 1.0), *((a, ALIAS_WEIGHT) for a in e["aliases"])]:
            score = _score(text_words, name, weight)
            if score > best:
                best, matched = score, name
        if best >= MIN_SCORE:
            found.append({**e, "score": round(best, 3), "matched": matched})
    found.sort(key=lambda e: (-e["score"], -e["mention_count"], e["key"]))
    return found[:limit]


def resolve_entity(ref: str, index: list[dict[str, Any]]) -> dict[str, Any]:
    """The index row `ref` names: a key, an exact name/alias, else the best fuzzy match.

    Raises ValueError when nothing matches or an exact name is shared by several entities.
    """
    ref = ref.strip()
    for e in index:
        if e["key"] == ref:
            return e
    wanted = words(ref)
    exact = [e for e in index if any(words(n) == wanted for n in [e["name"], *e["aliases"]])]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        options = ", ".join(f"{e['name']} [{e['key']}]" for e in exact[:5])
        raise ValueError(f"'{ref}' is ambiguous; use one of these keys: {options}")
    fuzzy = match_entities(ref, index, limit=1)
    if not fuzzy:
        raise ValueError(f"no entity matches '{ref}'; try find_entities first")
    return fuzzy[0]


def score_text(text: str, terms: list[str], phrase: str) -> float:
    """Relevance of a passage: distinct query words found, plus bonuses for the whole phrase and
    for repeats. Zero when no query word occurs."""
    lowered = text.lower()
    hits = [t for t in terms if re.search(rf"\b{re.escape(t)}", lowered)]
    if not hits:
        return 0.0
    score = float(len(hits)) / len(terms)
    if phrase and phrase in lowered:
        score += 1.0
    return score + 0.05 * min(sum(lowered.count(t) for t in hits), 10)
