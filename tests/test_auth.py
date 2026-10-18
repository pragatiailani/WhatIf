from types import SimpleNamespace

from datetime import datetime, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth
from app.api.auth import router

USER = {"name": "Ravi", "email": "Ravi@Example.com", "password": "secret-pass"}


@pytest.fixture
def client(mongo, monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_secret="test-secret"))
    from app.repo import users

    users.ensure_indexes()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_sign_up_sign_in_and_me(client):
    res = client.post("/auth/signup", json=USER)
    assert res.status_code == 201
    session = res.json()
    assert session["user"]["email"] == "ravi@example.com" and "password_hash" not in session["user"]

    signed_in = client.post(
        "/auth/signin", json={"email": "ravi@example.com", "password": "secret-pass"}
    )
    assert signed_in.status_code == 200

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {signed_in.json()['token']}"})
    assert me.json() == session["user"]


def test_rejects_bad_input(client):
    client.post("/auth/signup", json=USER)
    assert client.post("/auth/signup", json=USER).status_code == 409
    assert client.post("/auth/signup", json={**USER, "password": "short"}).status_code == 422
    assert client.post("/auth/signup", json={**USER, "email": "nope"}).status_code == 422
    assert (
        client.post(
            "/auth/signin", json={"email": USER["email"], "password": "wrong-pass"}
        ).status_code
        == 401
    )
    assert client.get("/auth/me").status_code == 401
    assert (
        client.get("/auth/me", headers={"Authorization": "Bearer forged.1.abc"}).status_code == 401
    )


def test_token_expiry_and_tampering(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_secret="test-secret"))
    token = auth.issue_token("u1")
    assert auth.verify_token(token) == "u1"

    header, payload, signature = token.split(".")
    forged = jwt.encode({**jwt.decode(token, options={"verify_signature": False}), "sub": "u2"}, "other-secret", algorithm="HS256")
    assert auth.verify_token(forged) is None  # signed with another key
    assert auth.verify_token(f"{header}.{payload}.{signature[:-2]}xx") is None  # signature altered
    unsigned = jwt.encode({"sub": "u1", "iss": "whatif", "iat": 1, "exp": 10**12}, None, algorithm="none")
    assert auth.verify_token(unsigned) is None  # `alg: none` is refused
    wrong_issuer = jwt.encode({"sub": "u1", "iss": "x", "iat": 1, "exp": 10**12}, "test-secret", algorithm="HS256")
    assert auth.verify_token(wrong_issuer) is None
    assert auth.verify_token("not-a-jwt") is None

    monkeypatch.setattr(auth, "ACCESS_TOKEN_TTL_SECONDS", -10)
    assert auth.verify_token(auth.issue_token("u1")) is None  # already expired


def test_refresh_rotates_the_cookie_and_returns_a_new_access_token(client):
    signed_up = client.post("/auth/signup", json=USER)
    cookie = signed_up.cookies.get("whatif_refresh")
    assert cookie and "httponly" in signed_up.headers["set-cookie"].lower()
    assert "path=/auth" in signed_up.headers["set-cookie"].lower()

    res = client.post("/auth/refresh")
    assert res.status_code == 200 and res.json()["user"] == signed_up.json()["user"]
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {res.json()['token']}"}).status_code == 200
    assert client.cookies.get("whatif_refresh") != cookie  # single use: replaced on every refresh

    client.cookies.set("whatif_refresh", cookie, path="/auth")  # replaying the old one fails
    assert client.post("/auth/refresh").status_code == 401


def test_refresh_without_a_cookie_and_logout(client):
    assert client.post("/auth/refresh").status_code == 401
    client.post("/auth/signup", json=USER)
    cookie = client.cookies.get("whatif_refresh")
    assert client.post("/auth/logout").status_code == 204
    client.cookies.set("whatif_refresh", cookie, path="/auth")
    assert client.post("/auth/refresh").status_code == 401  # revoked server side


def test_expired_refresh_token_is_rejected(client, mongo):
    client.post("/auth/signup", json=USER)
    mongo["refresh_tokens"].update_many({}, {"$set": {"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)}})
    assert client.post("/auth/refresh").status_code == 401


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/books"), ("get", "/books/b1"), ("get", "/books/b1/ingestion/events"),
        ("post", "/books"), ("get", "/books/b1/graph"), ("get", "/books/b1/storylines"),
        ("post", "/books/b1/storylines"), ("get", "/books/b1/messages"), ("get", "/books/b1/chats"),
        ("post", "/books/b1/chat"),
        ("get", "/storylines"), ("get", "/storylines/s1"), ("patch", "/storylines/s1"),
        ("post", "/sessions"), ("get", "/sessions/s1/messages"), ("post", "/sessions/s1/chat"),
        ("get", "/auth/me"), ("put", "/auth/me/inkitt"), ("delete", "/auth/me/inkitt"),
    ],
)
def test_every_api_route_requires_a_valid_access_token(mongo, monkeypatch, method, path):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_secret="test-secret"))
    monkeypatch.setenv("MONGO_URI", "mongodb://unused")
    from app.main import app

    client = TestClient(app)
    assert getattr(client, method)(path).status_code == 401
    assert getattr(client, method)(path, headers={"Authorization": "Bearer nope"}).status_code == 401
