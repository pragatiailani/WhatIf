import pytest
from fastapi.testclient import TestClient

from app.api import chat
from app.main import app
from app.repo import books, sessions
from app.repo.models import Book, Message


@pytest.fixture
def client(mongo, monkeypatch):
    monkeypatch.setenv("MONGO_URI", "mongodb://unused")
    monkeypatch.setenv("FALKOR_PASSWORD", "x")
    asked = []

    async def run(book_id, session_id, question, **_):
        asked.append((book_id, session_id, question))
        return {"answer": "A debt.", "citations": [{"chunk_id": "c1"}], "trace": [{"tool": "get_chunks"}]}

    monkeypatch.setattr(chat, "get_agent", lambda name: type("A", (), {"run": staticmethod(run)}))
    with TestClient(app) as c:
        c.asked = asked
        yield c


def test_chat_flow(client):
    book = books.create_book(
        Book(title="Kim", content_hash="h", status="ready", owner_id="u1", visibility="public")
    )
    # An uploaded (owned) public book is listed: no other router may shadow GET /books.
    assert [b["id"] for b in client.get("/books").json()] == [book.id]

    created = client.post("/sessions", json={"book_id": book.id})
    assert created.status_code == 201
    sid = created.json()["session_id"]

    reply = client.post(f"/sessions/{sid}/chat", json={"question": "Why did Ravi leave?"})
    assert reply.json() == {"answer": "A debt.", "citations": [{"chunk_id": "c1"}],
                            "trace": [{"tool": "get_chunks"}], "actions": []}
    assert client.asked == [(book.id, sid, "Why did Ravi leave?")]

    sessions.add_message(Message(session_id=sid, role="user", content="hi"))
    messages = client.get(f"/sessions/{sid}/messages").json()
    assert [m["content"] for m in messages] == ["hi"]


def test_errors(client):
    assert client.post("/sessions", json={"book_id": "nope"}).status_code == 404
    assert client.post("/sessions/nope/chat", json={"question": "hi"}).status_code == 404
    assert client.get("/sessions/nope/messages").status_code == 404
    session = client.post("/sessions", json={"book_id": books.create_book(
        Book(title="K", content_hash="h2")).id}).json()["session_id"]
    assert client.post(f"/sessions/{session}/chat", json={"question": ""}).status_code == 422
