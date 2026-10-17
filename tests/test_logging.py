"""Application logging: setup, request logging, and the log lines the pipelines and agent write."""

import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app import auth, ingestion, logging_config
from app.api import auth as auth_api
from app.api.middleware import REQUEST_ID_HEADER, RequestLogMiddleware
from app.llm.callbacks import LlmCallLogger
from app.logging_config import Timer, clip, fields, request_id, setup_logging
from app.repo import books
from app.repo.models import Book


@pytest.fixture
def clean_logging():
    """Undo setup_logging after a test so handlers do not pile up on the root logger."""
    yield
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, logging_config._HANDLER_FLAG, False)]:
        root.removeHandler(h)
        h.close()
    logging.getLogger("uvicorn.access").disabled = False


def ours():
    return [h for h in logging.getLogger().handlers if getattr(h, logging_config._HANDLER_FLAG, False)]


# --- setup ---------------------------------------------------------------


def test_setup_is_repeatable_and_adds_only_its_own_handlers(clean_logging, monkeypatch):
    monkeypatch.setattr(logging_config, "_settings", lambda: ("INFO", ""))
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)
    try:
        setup_logging("api")
        setup_logging("api")
        assert len(ours()) == 1 and foreign in logging.getLogger().handlers
    finally:
        logging.getLogger().removeHandler(foreign)


def test_level_comes_from_settings_unless_overridden_and_bad_values_fall_back(clean_logging, monkeypatch):
    monkeypatch.setattr(logging_config, "_settings", lambda: ("DEBUG", ""))
    setup_logging("cli")
    assert logging.getLogger().level == logging.DEBUG
    setup_logging("cli", level="warning")
    assert logging.getLogger().level == logging.WARNING
    monkeypatch.setattr(logging_config, "_settings", lambda: ("LOUD", ""))
    setup_logging("cli")
    assert logging.getLogger().level == logging.INFO


def test_noisy_libraries_are_quiet_unless_debugging(clean_logging, monkeypatch):
    monkeypatch.setattr(logging_config, "_settings", lambda: ("INFO", ""))
    setup_logging("api")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("uvicorn.access").disabled  # the request middleware replaces it
    monkeypatch.setattr(logging_config, "_settings", lambda: ("DEBUG", ""))
    setup_logging("mcp")
    assert logging.getLogger("httpx").level == logging.DEBUG


def test_file_logging_writes_the_request_id(clean_logging, monkeypatch, tmp_path):
    path = tmp_path / "nested" / "whatif.log"
    monkeypatch.setattr(logging_config, "_settings", lambda: ("INFO", str(path)))
    setup_logging("api")
    token = request_id.set("req-42")
    try:
        logging.getLogger("app.test").info("hello %s", "file")
    finally:
        request_id.reset(token)
    for h in ours():
        h.flush()
    line = next(l for l in path.read_text(encoding="utf-8").splitlines() if "hello file" in l)
    assert "INFO" in line and "app.test" in line and "[req-42]" in line


def test_logs_go_to_stderr_never_stdout(clean_logging, monkeypatch, capsys):
    monkeypatch.setattr(logging_config, "_settings", lambda: ("INFO", ""))
    setup_logging("mcp")  # the tool server speaks its protocol on stdout
    logging.getLogger("app.test").warning("careful")
    captured = capsys.readouterr()
    assert captured.out == "" and "careful" in captured.err


def test_settings_failure_falls_back_to_the_environment(monkeypatch):
    def broken():
        raise RuntimeError("invalid settings")

    monkeypatch.setattr("app.config.get_settings", broken)
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    monkeypatch.setenv("LOG_FILE", "")
    assert logging_config._settings() == ("ERROR", "")


def test_helpers():
    assert clip("a  b\n c") == "a b c" and clip("x" * 500, 10) == "x" * 9 + "…"
    assert fields({"book_id": "b1", "premise": "p" * 300}).startswith("book_id=b1 premise=ppp")
    assert len(fields({"premise": "p" * 300})) < 230
    with Timer() as t:
        pass
    assert t.ms >= 0


# --- requests ------------------------------------------------------------


@pytest.fixture
def app_client():
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)

    @app.get("/ok")
    def ok():
        return {"request_id": request_id.get()}  # what code inside a request sees

    @app.get("/missing")
    def missing():
        from fastapi import HTTPException

        raise HTTPException(404, "nope")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    return TestClient(app, raise_server_exceptions=False)


def test_each_request_gets_an_id_and_one_log_line(app_client, caplog):
    caplog.set_level(logging.DEBUG)
    res = app_client.get("/ok")
    rid = res.headers[REQUEST_ID_HEADER]
    assert len(rid) == 8
    line = next(r for r in caplog.records if r.name == "app.request")
    assert line.levelno == logging.INFO and "GET /ok -> 200" in line.getMessage()
    assert res.json()["request_id"] == rid  # every log line written inside the request carries it
    assert request_id.get() == "-"  # reset after the request


def test_a_client_supplied_request_id_is_kept(app_client):
    res = app_client.get("/ok", headers={REQUEST_ID_HEADER: "from-client"})
    assert res.headers[REQUEST_ID_HEADER] == "from-client"


def test_error_statuses_and_health_checks_use_the_right_levels(app_client, caplog):
    caplog.set_level(logging.DEBUG)
    app_client.get("/missing")
    app_client.get("/health")
    levels = {r.getMessage().split(" ->")[0]: r.levelno for r in caplog.records if r.name == "app.request"}
    assert levels["GET /missing"] == logging.INFO  # a 404 is the client's mistake, not ours
    assert levels["GET /health"] == logging.DEBUG


def test_an_unhandled_error_is_logged_with_a_traceback_and_the_id_to_quote(app_client, caplog):
    caplog.set_level(logging.DEBUG)
    res = app_client.get("/boom", headers={REQUEST_ID_HEADER: "abc123"})
    assert res.status_code == 500
    assert res.json() == {"detail": "Internal server error", "request_id": "abc123"}
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(r.exc_info and "kaboom" in str(r.exc_info[1]) for r in errors)
    assert any("GET /boom -> 500" in r.getMessage() for r in errors)


def test_real_app_exposes_the_request_id_header_and_logs_health_quietly(mongo, caplog):
    from app.main import app

    caplog.set_level(logging.INFO)
    res = TestClient(app).get("/health")
    assert res.headers[REQUEST_ID_HEADER]
    assert not [r for r in caplog.records if r.name == "app.request"]  # debug only


# --- auth never logs secrets --------------------------------------------


@pytest.fixture
def auth_client(mongo, monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_secret="test-secret"))
    from app.repo import users

    users.ensure_indexes()
    app = FastAPI()
    app.include_router(auth_api.router)
    return TestClient(app)


def test_auth_logs_user_ids_and_failures_without_emails_or_passwords(auth_client, caplog):
    caplog.set_level(logging.INFO)
    body = {"name": "Ravi", "email": "ravi@example.com", "password": "secret-pass"}
    assert auth_client.post("/auth/signup", json=body).status_code == 201
    assert auth_client.post("/auth/signup", json=body).status_code == 409
    assert auth_client.post("/auth/signin", json={**body, "password": "wrong-pass1"}).status_code == 401
    assert auth_client.post("/auth/signin", json={**body, "email": "nobody@example.com"}).status_code == 401
    assert auth_client.post("/auth/signin", json=body).status_code == 200

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "signup user=" in text and "signin user=" in text
    assert "signin failed: wrong password" in text and "signin failed: unknown email" in text
    assert "email already registered" in text
    for secret in ("ravi@example.com", "nobody@example.com", "secret-pass", "wrong-pass1", "test-secret"):
        assert secret not in text


# --- LLM calls -----------------------------------------------------------


def test_llm_calls_log_latency_and_tokens_but_not_the_prompt(caplog):
    caplog.set_level(logging.INFO)
    handler = LlmCallLogger("gpt-test")
    run = uuid4()
    handler.on_chat_model_start({}, [["SECRET PROMPT TEXT"]], run_id=run)
    message = AIMessage(content="SECRET ANSWER", usage_metadata={"input_tokens": 12, "output_tokens": 5, "total_tokens": 17})
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run)
    [line] = [r.getMessage() for r in caplog.records if r.name == "app.llm"]
    assert "model=gpt-test" in line and "tokens_in=12" in line and "tokens_out=5" in line
    assert "SECRET" not in line
    assert run not in handler._starts


def test_llm_failures_are_warnings(caplog):
    caplog.set_level(logging.INFO)
    handler = LlmCallLogger("gpt-test")
    run = uuid4()
    handler.on_llm_start({}, ["p"], run_id=run)
    handler.on_llm_error(RuntimeError("rate limited"), run_id=run)
    record = next(r for r in caplog.records if r.name == "app.llm")
    assert record.levelno == logging.WARNING and "rate limited" in record.getMessage()


def test_token_usage_from_llm_output_when_the_message_has_none():
    result = LLMResult(generations=[[]], llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}})
    from app.llm.callbacks import _usage

    assert _usage(result) == (7, 3)


# --- ingestion -----------------------------------------------------------


def parsed():
    return SimpleNamespace(page_count=3, chapters=[1], pages=[])


def test_a_failed_ingestion_logs_the_traceback_and_marks_the_book_failed(mongo, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    book = books.create_book(Book(title="Kim", content_hash="h"))
    monkeypatch.setattr(ingestion.chunks, "count_chunks", lambda _id: 3)
    monkeypatch.setattr(ingestion, "generate_page_graphs", lambda _id: (_ for _ in ()).throw(RuntimeError("llm down")))
    ingestion.ingest_book(book.id, parsed())
    assert books.get_book(book.id).status == "failed"
    messages = [r.getMessage() for r in caplog.records]
    assert any(f"ingestion started book={book.id}" in m for m in messages)
    assert any("stage=extracting" in m for m in messages)
    failure = next(r for r in caplog.records if "ingestion failed" in r.getMessage())
    assert failure.levelno == logging.ERROR and "llm down" in str(failure.exc_info[1])


def test_a_successful_ingestion_logs_each_stage_and_the_totals(mongo, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    book = books.create_book(Book(title="Kim", content_hash="h"))
    ok = SimpleNamespace(done=[0, 1], skipped=[], failed={}, blocked=[2], dropped=1)
    monkeypatch.setattr(ingestion.chunks, "count_chunks", lambda _id: 3)
    monkeypatch.setattr(ingestion, "generate_page_graphs", lambda _id: ok)
    monkeypatch.setattr(ingestion, "merge_page_graphs", lambda _id: SimpleNamespace(
        root=(2, 0), merged=[(1, 0)], skipped=[], failed={}, llm_calls=4))  # fmt: skip
    monkeypatch.setattr(ingestion, "publish_book_graph", lambda _id: {"entities": 5, "events": 7})
    ingestion.ingest_book(book.id, parsed())
    assert books.get_book(book.id).status == "ready"
    text = "\n".join(r.getMessage() for r in caplog.records)
    for stage in ("parsing", "extracting", "merging", "indexing"):
        assert f"stage={stage} book={book.id}" in text
    assert "done=2" in text and "blocked=1" in text and "llm_calls=4" in text
    assert "content filter blocked 1 page(s)" in text
    assert "ingestion finished" in text and "entities=5" in text


def test_extraction_failures_are_logged_per_page_and_the_ingestion_retries(mongo, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    book = books.create_book(Book(title="Kim", content_hash="h"))
    bad = SimpleNamespace(done=[], skipped=[], failed={4: "RateLimit"}, blocked=[], dropped=0)
    monkeypatch.setattr(ingestion.chunks, "count_chunks", lambda _id: 3)
    monkeypatch.setattr(ingestion, "generate_page_graphs", lambda _id: bad)
    ingestion.ingest_book(book.id, parsed())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert text.count("extraction pass") >= 6  # three passes, each logged and warned
    assert "left 1 failed page(s)" in text and "ingestion failed" in text
    assert "chunk 4: RateLimit" in books.get_book(book.id).error


def test_the_whole_module_set_has_loggers():
    """Every module that does real work has a logger named after it (so LOG_LEVEL can target it)."""
    import importlib

    for name in ("app.ingestion", "app.extraction.runner", "app.merge.runner", "app.merge.publish",
                 "app.storyline.draft", "app.storyline.verify", "app.storyline.prose",
                 "app.repo.graph", "app.agents.whatif", "app.api.books", "app.api.auth"):  # fmt: skip
        assert importlib.import_module(name).log.name == name

