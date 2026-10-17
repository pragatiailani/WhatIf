"""Find entities by name, and rank passages by keywords. Pure, no embeddings, no database.

Name search: whole-name phrases first, then shared name words, then names a typo away.
It runs over a `Linker`, lookup tables built once per entity index, so a question only touches
the entities that share a word with it instead of every entity of the book.
"""

import difflib
import math
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any

from app.constants import (
    FUZZY_MIN_WORD_LEN,
    FUZZY_NAME_CUTOFF,
    FUZZY_PENALTY,
    KEYWORD_TF_K1,
    RRF_K,
)
from app.extraction.schema import slug

try:  # much faster than difflib on big vocabularies, but optional
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover - exercised only without rapidfuzz installed
    fuzz = process = None

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
MAX_LINKERS = 8  # indexes kept with their lookup tables


def words(text: str) -> list[str]:
    """Lowercase ASCII words: "Zoë's cat" -> ['zoe', 's', 'cat']."""
    return [w for w in slug(text).split("_") if w]


def content_words(text: str) -> list[str]:
    return [w for w in words(text) if len(w) >= MIN_WORD_LEN and w not in STOPWORDS]


def _contains_phrase(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def _score_words(text_words: list[str], name_words: list[str], weight: float) -> float:
    if _contains_phrase(text_words, name_words):
        return weight
    significant = [w for w in name_words if len(w) >= MIN_WORD_LEN and w not in STOPWORDS]
    if not significant:
        return 0.0
    present = set(text_words)
    hit = sum(1 for w in significant if w in present)
    return PARTIAL_WEIGHT * weight * hit / len(significant)


def _score(text_words: list[str], name: str, weight: float) -> float:
    return _score_words(text_words, words(name), weight)


def _closest(word: str, vocabulary: list[str]) -> str | None:
    """The vocabulary word within the typo tolerance of `word`, if any (same first letter)."""
    pool = [v for v in vocabulary if v[0] == word[0]]
    if not pool:
        return None
    if process is not None:
        hit = process.extractOne(word, pool, scorer=fuzz.ratio, score_cutoff=FUZZY_NAME_CUTOFF)
        return hit[0] if hit else None
    close = difflib.get_close_matches(word, pool, n=1, cutoff=FUZZY_NAME_CUTOFF / 100)
    return close[0] if close else None


class Linker:
    """Lookup tables over one entity index (rows of key, name, type, aliases, mention_count)."""

    def __init__(self, index: Sequence[Mapping[str, Any]]):
        self.index = index
        self.by_key = {e["key"]: i for i, e in enumerate(index)}
        self.entries: list[list[tuple[list[str], float, str]]] = []  # per entity: (words, weight, name)
        self.by_word: dict[str, list[int]] = {}  # name word -> entities having it
        self.exact: dict[tuple[str, ...], list[int]] = {}  # all words of a name -> entities
        for i, e in enumerate(index):
            names = [(e["name"], 1.0), *((a, ALIAS_WEIGHT) for a in e["aliases"])]
            entries = [(words(n), w, n) for n, w in names]
            self.entries.append(entries)
            reachable: set[str] = set()
            for name_words, _, _ in entries:
                if not name_words:
                    continue
                hold = self.exact.setdefault(tuple(name_words), [])
                if i not in hold:
                    hold.append(i)
                # Stopwords alone never make a candidate, unless the name is nothing but them.
                reachable |= {w for w in name_words if w not in STOPWORDS} or set(name_words)
            for w in reachable:
                self.by_word.setdefault(w, []).append(i)
        self.vocabulary = [w for w in self.by_word if len(w) >= FUZZY_MIN_WORD_LEN]

    def _best(self, text_words: list[str], candidates: set[int]) -> dict[int, tuple[float, str]]:
        found: dict[int, tuple[float, str]] = {}
        for i in candidates:
            best, matched = 0.0, ""
            for name_words, weight, name in self.entries[i]:
                score = _score_words(text_words, name_words, weight)
                if score > best:
                    best, matched = score, name
            if best > 0:
                found[i] = (best, matched)
        return found

    def _typo_fixes(self, text_words: list[str]) -> dict[str, str]:
        """Question words that are no name word but a typo away from one."""
        fixes: dict[str, str] = {}
        for w in dict.fromkeys(text_words):
            if len(w) < FUZZY_MIN_WORD_LEN or w in STOPWORDS or w in self.by_word:
                continue
            close = _closest(w, self.vocabulary)
            if close:
                fixes[w] = close
        return fixes

    def match(self, text: str, limit: int) -> list[dict[str, Any]]:
        text_words = words(text)
        candidates = {i for w in set(text_words) for i in self.by_word.get(w, ())}
        scored = self._best(text_words, candidates)
        fixes = self._typo_fixes(text_words)
        if fixes:
            fixed = [fixes.get(w, w) for w in text_words]
            fuzzy_candidates = {i for w in set(fixed) for i in self.by_word.get(w, ())}
            for i, (score, matched) in self._best(fixed, fuzzy_candidates).items():
                score *= FUZZY_PENALTY
                if score > scored.get(i, (0.0, ""))[0]:
                    scored[i] = (score, matched)
        found = [
            {**self.index[i], "score": round(score, 3), "matched": matched}
            for i, (score, matched) in scored.items()
            if score >= MIN_SCORE
        ]
        found.sort(key=lambda e: (-e["score"], -e["mention_count"], e["key"]))
        return found[:limit]


_LINKERS: "OrderedDict[int, tuple[Sequence[Mapping[str, Any]], Linker]]" = OrderedDict()


def linker_for(index: Sequence[Mapping[str, Any]]) -> Linker:
    """The `Linker` of `index`, built once per index object (the entity index is cached, so a
    book's tables are reused across tool calls). The index must not be modified afterwards."""
    hit = _LINKERS.get(id(index))
    if hit is not None and hit[0] is index:
        _LINKERS.move_to_end(id(index))
        return hit[1]
    linker = Linker(index)
    _LINKERS[id(index)] = (index, linker)  # holding the list keeps its id from being reused
    while len(_LINKERS) > MAX_LINKERS:
        _LINKERS.popitem(last=False)
    return linker


def match_entities(text: str, index: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Entities whose name or alias appears in `text`, best first (ties: most mentioned).

    Each result is an index row plus `score` and `matched` (the name or alias that matched).
    A question word a small typo away from a name word counts too, at a slightly lower score.
    """
    return linker_for(index).match(text, limit)


def resolve_entity(ref: str, index: list[dict[str, Any]]) -> dict[str, Any]:
    """The index row `ref` names: a key, an exact name/alias, else the best fuzzy match.

    Raises ValueError when nothing matches or an exact name is shared by several entities.
    """
    linker = linker_for(index)
    ref = ref.strip()
    if ref in linker.by_key:
        return index[linker.by_key[ref]]
    wanted = tuple(words(ref))
    exact = linker.exact.get(wanted, []) if wanted else []
    if len(exact) == 1:
        return index[exact[0]]
    if len(exact) > 1:
        options = ", ".join(f"{index[i]['name']} [{index[i]['key']}]" for i in exact[:5])
        raise ValueError(f"'{ref}' is ambiguous; use one of these keys: {options}")
    fuzzy = linker.match(ref, limit=1)
    if not fuzzy:
        raise ValueError(f"no entity matches '{ref}'; try find_entities first")
    return fuzzy[0]


# --- passages ------------------------------------------------------------


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


def idf_weights(terms: Sequence[str], doc_counts: Mapping[str, int], n_docs: int) -> dict[str, float]:
    """How telling each query word is: rare words weigh more than words found everywhere."""
    return {
        t: math.log(1 + (n_docs - doc_counts.get(t, 0) + 0.5) / (doc_counts.get(t, 0) + 0.5))
        for t in terms
    }


def score_chunk(text: str, terms: Sequence[str], idf: Mapping[str, float], phrase: str = "") -> float:
    """BM25-style relevance of a passage: each query word counts by its weight, with repeats
    adding less each time, plus a bonus when the whole query appears as a phrase. 0 if no query
    word occurs. Scores are only comparable within one query."""
    lowered = text.lower()
    total = sum(idf.get(t, 0.0) for t in terms) or 1.0
    score = 0.0
    for t in terms:
        tf = len(re.findall(rf"\b{re.escape(t)}", lowered))
        if tf:
            score += idf.get(t, 0.0) * tf * (KEYWORD_TF_K1 + 1) / (tf + KEYWORD_TF_K1)
    if score == 0.0:
        return 0.0
    score /= total
    if phrase and len(terms) > 1 and phrase in lowered:
        score += 0.5
    return score


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal rank fusion: merge several best-first rankings of ids into one score per id.
    An id near the top of several rankings beats one that tops a single ranking."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, id_ in enumerate(ranking, start=1):
            fused[id_] = fused.get(id_, 0.0) + 1.0 / (k + rank)
    return fused
