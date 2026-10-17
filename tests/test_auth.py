from types import SimpleNamespace

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
    assert auth.verify_token(token.replace("u1", "u2", 1)) is None
    monkeypatch.setattr(auth.time, "time", lambda: 10**12)
    assert auth.verify_token(token) is None
