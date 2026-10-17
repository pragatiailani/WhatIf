"""Map an LLM evidence quote back to the chunk text, then to a page.

The offsets and the page come from the chunk and its `page_spans`; the LLM only supplies the
quote. A quote that cannot be found in the chunk is rejected, so ungrounded items can be dropped.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.constants import MIN_QUOTE_MATCH
from app.repo.models import Chunk

_MIN_BLOCK = 4  # ignore matching runs shorter than this when fuzzy matching
_MAX_SPREAD = 1.5  # a fuzzy match may span at most this many times the quote length


@dataclass(frozen=True)
class Evidence:
    char_start: int  # offsets within the chunk text
    char_end: int
    page: int
    quote: str  # the matched chunk text (not the LLM's wording)
    score: float  # 1.0 = exact


def page_at(chunk: Chunk, offset: int) -> int | None:
    """Page containing `offset` (chunk-relative); None if the chunk has no page info."""
    for span in chunk.page_spans:
        if span.char_start <= offset < span.char_end:
            return span.page
    return chunk.page_spans[-1].page if chunk.page_spans else None


def _find_span(text: str, quote: str) -> tuple[int, int, float] | None:
    exact = text.find(quote)
    if exact >= 0:
        return exact, exact + len(quote), 1.0

    # Same words, different whitespace (line breaks, double spaces).
    words = quote.split()
    if words:
        pattern = r"\s+".join(re.escape(w) for w in words)
        found = re.search(pattern, text)
        if found:
            return found.start(), found.end(), 1.0

    # Fuzzy: tolerate curly quotes, dropped punctuation, small rewordings.
    matcher = SequenceMatcher(None, text, quote, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size >= _MIN_BLOCK]
    if not blocks:
        return None
    score = sum(b.size for b in blocks) / len(quote)
    if score < MIN_QUOTE_MATCH:
        return None
    first, last = blocks[0], blocks[-1]
    start = max(0, first.a - first.b)
    end = min(len(text), last.a + last.size + (len(quote) - (last.b + last.size)))
    if end - start > len(quote) * _MAX_SPREAD + 10:
        return None
    return start, end, min(1.0, score)


def locate_quote(chunk: Chunk, quote: str) -> Evidence | None:
    """Find `quote` in `chunk.text` and return its offsets and page, or None if ungrounded."""
    quote = quote.strip()
    if not quote:
        return None
    span = _find_span(chunk.text, quote)
    if span is None:
        return None
    start, end, score = span
    page = page_at(chunk, start)
    if page is None:
        return None
    return Evidence(char_start=start, char_end=end, page=page, quote=chunk.text[start:end], score=score)
