"""A storyline read as a book, for publishing (see `app.inkitt.storyline_story`).

From the first chapter the storyline changes to the end, each chapter is the storyline's version:
the written passage if the chapter changed, the original text if it did not. Chapters before that
are the base book's, which the publisher keeps itself.

Nothing here calls a model. A changed chapter with no current passage cannot be published, so the
reader is told which chapters to write first (`read_chapter` or `generate_passage` writes them).
"""

import logging

from app import inkitt
from app.repo import chunks, storylines
from app.repo.storylines import StorylinesUnavailable, StorylineText
from app.storyline.prose import ChapterView, current_passage

log = logging.getLogger(__name__)


def storyline_text(storyline_id: str) -> StorylineText | None:
    sl = storylines.get_storyline(storyline_id)
    if sl is None:
        return None
    if sl.draft is not None:
        raise StorylinesUnavailable(
            "This storyline has an open draft. Commit it (or discard it) before publishing."
        )
    titles = [c["title"] for c in chunks.list_chapters(sl.book_id)]
    views = {t: ChapterView(sl, t) for t in titles}
    changed = [t for t in titles if views[t].changed]
    if not changed:
        return StorylineText(sl.id, sl.book_id, sl.owner_id or "", sl.name, None, [])

    first = titles.index(changed[0])
    original = dict(inkitt.book_chapters(sl.book_id))
    chapters: list[tuple[str, str]] = []
    unwritten: list[str] = []
    for title in titles[first:]:
        if not views[title].changed:
            chapters.append((title, original.get(title, "")))
        elif (passage := current_passage(sl, views[title])) is not None:
            chapters.append((title, passage.text))
        else:
            unwritten.append(title)
    if unwritten:
        log.info("storyline not publishable storyline=%s unwritten=%s", sl.id, unwritten)
        raise StorylinesUnavailable(
            "These chapters changed in the storyline but have not been written yet: "
            + ", ".join(unwritten)
            + ". Write them first with read_chapter or generate_passage, then try again."
        )
    return StorylineText(sl.id, sl.book_id, sl.owner_id or "", sl.name, changed[0], chapters)
