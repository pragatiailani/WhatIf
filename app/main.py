import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import repo
from app.api import auth, books, chat, memories, reading
from app.constants import DEFAULT_CORS_ORIGIN_REGEX
from app.api.middleware import REQUEST_ID_HEADER, RequestLogMiddleware
from app.logging_config import setup_logging

setup_logging("api")  # at import, so reload workers are configured too
log = logging.getLogger(__name__)

INTERRUPTED_ERROR = (
    "Ingestion was interrupted by a server restart. Upload the file again to resume."
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.config import get_settings
    from app.repo import ensure_indexes
    from app.repo.books import fail_unfinished

    s = get_settings()
    log.info("starting host=%s port=%s mongo_db=%s falkor=%s:%s debug=%s",
             s.server_host, s.server_port, s.mongo_db, s.falkor_host, s.falkor_port, s.debug)
    try:
        ensure_indexes()
        if n := fail_unfinished(INTERRUPTED_ERROR):
            log.warning("marked %d interrupted ingestion(s) as failed", n)
        log.info("mongo ready")
    except Exception:  # the API still starts so /health works while MongoDB is down
        log.exception("could not prepare MongoDB")
    yield
    log.info("shutting down")


app = FastAPI(
    title="WhatIf?",
    description="A chatbot that turns a book into a knowledge graph with customizable storylines",
    version="0.1.0",
    lifespan=lifespan,
)

def _cors_origins() -> list[str]:
    from app.config import get_settings

    try:
        extra = get_settings().cors_origins
    except Exception:  # settings unavailable (e.g. no .env): the defaults still work for local dev
        extra = ""
    return [o.strip().rstrip("/") for o in extra.split(",") if o.strip()]


# CORS middleware. The refresh cookie needs credentials, which browsers refuse with a "*" origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=DEFAULT_CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[REQUEST_ID_HEADER],
)
# added last, so it is the outermost layer and sees every request, preflights included
app.add_middleware(RequestLogMiddleware)


@app.get("/")
async def root():
    return {
        "message": "WhatIf? API",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(books.router)
app.include_router(reading.router)
app.include_router(reading.storylines_router)
app.include_router(memories.router)

if __name__ == "__main__":
    import uvicorn

    from app.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug,
    )
