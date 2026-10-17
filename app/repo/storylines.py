"""Storylines as the Inkitt publisher reads them.

Placeholder until the storyline feature (README, "Storyline update") stores storylines and their
generated passages. Only `get_storyline_text` is needed for publishing; implement it from the
storyline repo and the rest works unchanged.
"""

from dataclasses import dataclass, field


class StorylinesUnavailable(Exception):
    """Storylines are not stored yet, so none can be read."""


@dataclass(frozen=True)
class StorylineText:
    id: str
    book_id: str  # the base book
    owner_id: str
    title: str
    # Title of the first base-book chapter the storyline rewrites: base chapters before it are kept
    # as they are. None keeps every base chapter (the storyline continues after the book's end).
    diverges_at: str | None
    chapters: list[tuple[str, str]] = field(default_factory=list)  # new (title, markdown text)


def get_storyline_text(storyline_id: str) -> StorylineText | None:
    """The storyline's metadata and new chapters, or None if there is no such storyline."""
    raise StorylinesUnavailable(
        "Storylines can't be published yet: they are not saved on the server. "
        "The book itself can still be published."
    )
