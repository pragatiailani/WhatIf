"""Chat with the book agent from the terminal.

    uv run python -m scripts.chat                     # pick a book, start a new chat
    uv run python -m scripts.chat --book <book_id>
    uv run python -m scripts.chat --session <id>      # resume an earlier chat

Needs MongoDB and FalkorDB running and the LLM settings in .env. In the chat: /history, /new, /quit.
"""

import argparse
import asyncio
import os
import sys

from app.agents import get_agent
from app.logging_config import setup_logging
from app.repo import books, ensure_indexes, sessions
from app.repo.models import Book, ChatSession

AGENT = "whatif"
DIM, RESET = "\033[2m", "\033[0m"


def pick_book() -> Book:
    catalog = books.list_books()
    if not catalog:
        raise SystemExit("No books found. Ingest a book first.")
    if len(catalog) == 1:
        return catalog[0]
    for i, b in enumerate(catalog, 1):
        print(f"  {i}. {b.title}" + (f" by {b.author}" if b.author else "") + f"  [{b.status}]")
    while True:
        choice = input("Book number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(catalog):
            return catalog[int(choice) - 1]


def show_citations(citations: list[dict]) -> None:
    for c in citations:
        pages = c.get("page_start")
        if pages is not None and c.get("page_end") not in (None, pages):
            pages = f"{pages}-{c['page_end']}"
        print(f"{DIM}  [{c.get('chapter') or 'no chapter'}, p. {pages}]  chunk {c['chunk_id']}{RESET}")


def show_history(session_id: str) -> None:
    for m in sessions.last_messages(session_id, 20):
        print(f"{'you' if m.role == 'user' else 'bot'}> {m.content}")


async def chat_loop(book: Book, session: ChatSession) -> None:
    run = get_agent(AGENT).run
    print(f"\nChatting about '{book.title}' (session {session.id}). /history /new /quit\n")
    while True:
        try:
            question = (await asyncio.to_thread(input, "you> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question:
            continue
        if question in ("/quit", "/exit"):
            return
        if question == "/history":
            show_history(session.id)
            continue
        if question == "/new":
            session = sessions.create_session(ChatSession(book_id=book.id))
            print(f"new session {session.id}")
            continue
        try:
            result = await run(book.id, session.id, question)
        except Exception as exc:  # keep the chat alive; the error is usually setup (DB, LLM keys)
            print(f"error: {exc!r}")
            continue
        tools = ", ".join(t["tool"] for t in result["trace"]) or "none"
        print(f"{DIM}  tools: {tools}{RESET}")
        print(f"bot> {result['answer']}")
        show_citations(result["citations"])


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--book", help="book id")
    parser.add_argument("--session", help="resume this session")
    args = parser.parse_args()

    # the chat prints its own output, so keep the console quiet unless LOG_LEVEL is set explicitly
    setup_logging("cli", level=None if os.environ.get("LOG_LEVEL") else "WARNING")
    ensure_indexes()
    if args.session:
        session = sessions.get_session(args.session)
        if session is None:
            raise SystemExit(f"unknown session '{args.session}'")
        book = books.get_book(session.book_id)
    else:
        book = books.get_book(args.book) if args.book else pick_book()
        session = sessions.create_session(ChatSession(book_id=book.id)) if book else None
    if book is None or session is None:
        raise SystemExit(f"unknown book '{args.book}'")
    await chat_loop(book, session)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
