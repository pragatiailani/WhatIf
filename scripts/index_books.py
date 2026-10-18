"""Embed books for semantic search: books ingested before embeddings, or after a model change.

    uv run python -m scripts.index_books              # every ready book
    uv run python -m scripts.index_books --book <id>

Needs MongoDB, the LLM settings and AZURE_EMBEDDING_MODEL in .env. Re-indexing replaces a book's
vectors, so it is safe to rerun.
"""

import argparse

from app import retrieval
from app.repo import books, ensure_indexes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--book", help="index only this book id")
    args = parser.parse_args()
    if not retrieval.enabled():
        raise SystemExit("Set AZURE_EMBEDDING_MODEL in .env to an embedding deployment first.")
    ensure_indexes()

    targets = [books.get_book(args.book)] if args.book else books.list_all()
    for book in targets:
        if book is None:
            raise SystemExit(f"unknown book '{args.book}'")
        if book.status != "ready":
            print(f"skip  {book.title} [{book.status}]")
            continue
        stats = retrieval.index_book(book.id)
        books.update_stats(book.id, stats)
        print(f"done  {book.title}: {stats.get('vectors', 0)} vectors")


if __name__ == "__main__":
    main()
