"""Build community summaries for books ingested before communities existed, or rebuild them.

    uv run python -m scripts.build_communities              # every ready book
    uv run python -m scripts.build_communities --book <id>

Needs MongoDB, FalkorDB and the LLM settings in .env; one LLM call per group plus one overview per
book. With AZURE_EMBEDDING_MODEL set, the summaries are embedded for semantic search too.
Rebuilding replaces a book's communities, so it is safe to rerun.
"""

import argparse
import asyncio

from app import retrieval
from app.communities import abuild_communities
from app.repo import books, ensure_indexes


async def run(book_id: str | None) -> None:
    """All books in one event loop: the cached chat model's async client is bound to the first."""
    targets = [books.get_book(book_id)] if book_id else books.list_all()
    for book in targets:
        if book is None:
            raise SystemExit(f"unknown book '{book_id}'")
        if book.status != "ready":
            print(f"skip  {book.title} [{book.status}]")
            continue
        stats = await abuild_communities(book.id)
        if retrieval.enabled():
            retrieval.index_communities(book.id)
        books.update_stats(book.id, stats)
        print(f"done  {book.title}: {stats['communities']} communities")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--book", help="build only this book id")
    args = parser.parse_args()
    ensure_indexes()
    asyncio.run(run(args.book))


if __name__ == "__main__":
    main()
