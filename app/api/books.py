import asyncio
import hashlib
import json
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import computed_field

from app.api.auth import current_user, optional_user
from app.ingestion import ingest_book, progress
from app.repo import books
from app.repo.models import Book, User, Visibility
from app.repo.service.parsing import PDFParseError, ScannedPDFError, parse_pdf

router = APIRouter(prefix="/books", tags=["books"])

PROGRESS_POLL_SECONDS = 2


class BookOut(Book):
    @computed_field
    @property
    def cover_hue(self) -> int:
        """Stable cover colour for the UI, derived from the id."""
        return int(self.id[:6], 16) % 360


def _out(book: Book) -> BookOut:
    return BookOut.model_validate(book.model_dump())


def _get(book_id: str) -> Book:
    book = books.get_book(book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    return book


@router.get("", response_model=list[BookOut], response_model_by_alias=False)
def list_books(
    user: Annotated[User | None, Depends(optional_user)],
    scope: Literal["public", "mine"] = "public",
) -> list[BookOut]:
    """`public`: the public library, without failed books. `mine`: the signed-in user's uploads,
    public or private, failed ones included so they can be uploaded again."""
    if scope == "public":
        return [_out(b) for b in books.list_by_visibility("public") if b.status != "failed"]
    if user is None:
        raise HTTPException(401, "sign in required")
    return [_out(b) for b in books.list_owned_books(user.id)]


@router.get("/{book_id}", response_model=BookOut, response_model_by_alias=False)
def get_book(book_id: str) -> BookOut:
    return _out(_get(book_id))


@router.get("/{book_id}/ingestion/events")
async def ingestion_events(book_id: str) -> StreamingResponse:
    """Server-Sent Events: the book's ingestion progress, sent on change until ready or failed."""
    await run_in_threadpool(_get, book_id)

    async def stream():
        last = None
        while True:
            p = await run_in_threadpool(lambda: progress(_get(book_id)))
            if p != last:
                yield f"data: {json.dumps(p)}\n\n"
                last = p
            if p["status"] in ("ready", "failed"):
                return
            await asyncio.sleep(PROGRESS_POLL_SECONDS)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("", status_code=202, response_model=BookOut, response_model_by_alias=False)
def upload_book(
    background: BackgroundTasks,
    user: Annotated[User, Depends(current_user)],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    author: Annotated[str | None, Form()] = None,
    visibility: Annotated[Visibility, Form()] = "public",
) -> BookOut:
    """Upload a PDF and ingest it in the background. Watch `/ingestion/events` for progress.

    Re-uploading the same file returns the user's existing book; a failed one is ingested again.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(415, "only PDF files are supported")
    data = file.file.read()
    existing = books.find_by_content_hash(hashlib.sha256(data).hexdigest(), user.id)
    if existing and existing.status != "failed":
        return _out(existing)

    try:
        parsed = parse_pdf(data)
    except ScannedPDFError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PDFParseError as exc:
        raise HTTPException(400, str(exc)) from exc

    book = existing or books.create_book(
        Book(
            title=title or parsed.title or file.filename[:-4],
            author=author or parsed.author,
            content_hash=parsed.content_hash,
            owner_id=user.id,
            visibility=visibility,
        )
    )
    background.add_task(ingest_book, book.id, parsed)
    return _out(book)
