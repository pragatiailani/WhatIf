"""Application logging: one setup for the API, the CLI and the tool-server process.

Logs go to stderr (the tool server speaks its protocol on stdout, so nothing here may print there)
and, if `LOG_FILE` is set, to a rotating file. Every line carries the id of the request that caused
it (`-` outside a request) so one upload or chat message can be followed through the logs.

Never log secrets: passwords, tokens, API keys, or whole book text.
"""

import contextvars
import logging
import logging.handlers
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.constants import (
    LOG_FILE_BACKUPS,
    LOG_FILE_MAX_BYTES,
    LOG_FORMAT,
    LOG_VALUE_CHARS,
    NOISY_LOGGERS,
)

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_HANDLER_FLAG = "_whatif_handler"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


def _settings() -> tuple[str, str]:
    """(level, file) from the settings, falling back to the environment if they are invalid."""
    try:
        from app.config import get_settings

        s = get_settings()
        return s.log_level, s.log_file
    except Exception:  # logging must work even when the rest of the config is broken
        return os.environ.get("LOG_LEVEL", "INFO"), os.environ.get("LOG_FILE", "")


def setup_logging(service: str = "api", level: str | None = None) -> None:
    """Configure the root logger. Safe to call again: it replaces only the handlers it added.

    `level` overrides `LOG_LEVEL` (the CLI uses it to keep the console quiet).
    """
    configured_level, log_file = _settings()
    resolved = logging.getLevelName((level or configured_level).upper())
    if not isinstance(resolved, int):
        resolved = logging.INFO

    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_FLAG, False)]:
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                path, maxBytes=LOG_FILE_MAX_BYTES, backupCount=LOG_FILE_BACKUPS, encoding="utf-8"
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_RequestIdFilter())
        setattr(handler, _HANDLER_FLAG, True)
        root.addHandler(handler)
    root.setLevel(resolved)

    quiet = logging.WARNING if resolved > logging.DEBUG else logging.DEBUG
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(quiet)
    if service == "api":  # the request middleware logs each request with its id and duration
        logging.getLogger("uvicorn.access").disabled = True
    # the tool server starts for every message, so only the API announces itself at info level
    logging.getLogger(__name__).log(
        logging.INFO if service == "api" else logging.DEBUG,
        "logging ready service=%s level=%s file=%s", service, logging.getLevelName(resolved), log_file or "-",
    )


def clip(value: Any, limit: int = LOG_VALUE_CHARS) -> str:
    """A value shortened for a log line."""
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fields(values: Mapping[str, Any]) -> str:
    """`key=value` pairs for a log line, with long values clipped."""
    return " ".join(f"{k}={clip(v)}" for k, v in values.items())


class Timer:
    """`with Timer() as t: ...` then `t.ms` is the elapsed milliseconds."""

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        self.ms = 0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = round((time.perf_counter() - self._start) * 1000)
