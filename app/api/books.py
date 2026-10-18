import asyncio
import hashlib
import json
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import computed_field

from app.api.auth import current_user
from app.ingestion import ingest_book, progress
from app.repo import books
from app.repo.models import Book, User, Visibility
from app.repo.service.parsing import PDFParseError, ScannedPDFError, parse_pdf

router = APIRouter(prefix="/books", tags=["books"], dependencies=[Depends(current_user)])
log = logging.getLogger(__name__)

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
    user: Annotated[User, Depends(current_user)],
    scope: Literal["public", "mine"] = "public",
) -> list[BookOut]:
    """`public`: the public library, without failed books. `mine`: the signed-in user's uploads,
    public or private, failed ones included so they can be uploaded again."""
    if scope == "public":
        return [_out(b) for b in books.list_by_visibility("public") if b.status != "failed"]
    return [_out(b) for b in books.list_owned_books(user.id)]


@router.get("/{book_id}", response_model=BookOut, response_model_by_alias=False)
def get_book(book_id: str) -> BookOut:
    return _out(_get(book_id))


@router.get("/{book_id}/ingestion/events")
async def ingestion_events(book_id: str) -> StreamingResponse:
    """Server-Sent Events: the book's ingestion progress, sent on change until ready or failed."""
    await run_in_threadpool(_get, book_id)
    log.debug("progress stream opened book=%s", book_id)

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
        log.warning("upload rejected: not a PDF (%s)", file.filename)
        raise HTTPException(415, "only PDF files are supported")
    data = file.file.read()
    existing = books.find_by_content_hash(hashlib.sha256(data).hexdigest(), user.id)
    if existing and existing.status != "failed":
        log.info("upload is a duplicate of book=%s status=%s user=%s", existing.id, existing.status, user.id)
        return _out(existing)

    try:
        parsed = parse_pdf(data)
    except ScannedPDFError as exc:
        log.warning("upload rejected: scanned PDF (%s, %d bytes)", file.filename, len(data))
        raise HTTPException(422, str(exc)) from exc
    except PDFParseError as exc:
        log.warning("upload rejected: unreadable PDF (%s): %s", file.filename, exc)
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
    log.info("upload accepted book=%s user=%s bytes=%d pages=%s retry=%s", book.id, user.id,
             len(data), parsed.page_count, existing is not None)
    background.add_task(ingest_book, book.id, parsed)
    return _out(book)
