from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth, ingestion
from app.api import auth as auth_api
from app.api import books as books_api
from app.repo import books, chunks
from tests.test_parsing import _make_pdf


@pytest.fixture
def client(mongo, monkeypatch):
    from fastapi import FastAPI

    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_secret="test-secret"))
    monkeypatch.setattr(ingestion, "generate_page_graphs", lambda _: SimpleNamespace(failed={}, blocked=[]))
    monkeypatch.setattr(ingestion, "merge_page_graphs", lambda _: SimpleNamespace(root=(1, 0)))
    monkeypatch.setattr(ingestion, "publish_book_graph", lambda _: {"entities": 3, "events": 2})
    app = FastAPI()
    app.include_router(auth_api.router)
    app.include_router(books_api.router)
    c = TestClient(app)
    body = {"name": "Ravi", "email": "ravi@example.com", "password": "secret-pass"}
    token = c.post("/auth/signup", json=body).json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def _upload(client, data, **form):
    return client.post("/books", files={"file": ("novel.pdf", data, "application/pdf")}, data=form)


def test_upload_runs_pipeline(client):
    pdf = _make_pdf(
        [[f"Line {i} of page {p} with plenty of words." for i in range(4)] for p in range(3)]
    )
    res = _upload(client, pdf, title="My Book")
    assert res.status_code == 202
    body = res.json()
    assert body["title"] == "My Book" and "id" in body

    book = books.get_book(body["id"])
    assert book.status == "ready"
    assert chunks.count_chunks(book.id) == 3
    assert book.stats == {"pages": 3, "chapters": 0, "chunks": 3, "entities": 3, "events": 2}

    # Same file again: existing book, no new ingestion.
    assert _upload(client, pdf).json()["id"] == book.id


def test_failed_step_marks_book_failed(client, monkeypatch):
    monkeypatch.setattr(
        ingestion, "merge_page_graphs", lambda _: SimpleNamespace(root=None, failed={(1, 0): "x"})
    )
    pdf = _make_pdf([["Some page text that is long enough to parse fine."]])
    book = books.get_book(_upload(client, pdf).json()["id"])
    assert book.status == "failed" and "merge failed" in book.error


def test_bad_pdf_rejected(client):
    assert _upload(client, b"not a pdf").status_code == 400


def test_non_pdf_rejected(client):
    res = client.post("/books", files={"file": ("novel.txt", b"hello", "text/plain")})
    assert res.status_code == 415


def test_upload_requires_sign_in(client):
    del client.headers["Authorization"]
    assert _upload(client, b"%PDF").status_code == 401
    assert client.get("/books?scope=mine").status_code == 401


def test_list_get_and_progress(client):
    pdf = _make_pdf([["A page of story text that is long enough to parse."]])
    public = _upload(client, pdf).json()
    private = _upload(
        client, _make_pdf([["Another, private story page with text."]]), visibility="private"
    ).json()
    assert public["visibility"] == "public" and 0 <= public["cover_hue"] < 360
    assert public["owner_id"] == private["owner_id"] is not None

    assert [b["id"] for b in client.get("/books").json()] == [public["id"]]
    assert {b["id"] for b in client.get("/books?scope=mine").json()} == {
        public["id"],
        private["id"],
    }
    assert client.get(f"/books/{public['id']}").json()["stats"]["pages"] == 1
    assert client.get("/books/nope").status_code == 404

    with client.stream("GET", f"/books/{public['id']}/ingestion/events") as res:
        events = [line for line in res.iter_lines() if line.startswith("data:")]
    assert len(events) == 1 and '"status": "ready"' in events[0]


def test_extraction_retries_failed_pages(client, monkeypatch):
    passes = iter([SimpleNamespace(failed={3: "429"}, blocked=[1]), SimpleNamespace(failed={}, blocked=[])])
    monkeypatch.setattr(ingestion, "generate_page_graphs", lambda _: next(passes))
    book = books.get_book(_upload(client, _make_pdf([["A page of story text, long enough."]])).json()["id"])
    assert book.status == "ready" and book.stats["blocked_pages"] == 1
