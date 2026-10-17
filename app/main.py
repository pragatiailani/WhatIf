from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import repo
from app.api import auth, books


@asynccontextmanager
async def lifespan(_: FastAPI):
    repo.ensure_indexes()
    yield


app = FastAPI(
    title="WhatIf?",
    description="A chatbot that turns a book into a knowledge graph with customizable storylines",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


app.include_router(auth.router)
app.include_router(books.router)


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
