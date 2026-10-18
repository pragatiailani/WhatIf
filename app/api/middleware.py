"""Request logging: an id for every request, and one line per request with status and duration."""

import logging
import time
import uuid

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import request_id

log = logging.getLogger("app.request")
REQUEST_ID_HEADER = "X-Request-ID"
QUIET_PATHS = frozenset({"/health"})  # polled often; logged at DEBUG


class RequestLogMiddleware:
    """Pure ASGI, so streamed responses (ingestion progress) and background tasks are unaffected.

    The request id is set for the whole request, including background tasks started by it, so
    an upload and the ingestion it triggers share an id. A client may send its own `X-Request-ID`.

    It also catches unexpected errors itself. FastAPI's catch-all handler runs outside any
    middleware, after the id is gone, so the traceback and the 500 response would lose it.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = (Headers(scope=scope).get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:8])[:64]
        token = request_id.set(rid)
        method, path = scope["method"], scope["path"]
        status = 500
        started = time.perf_counter()
        response_started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_id)
            except Exception:
                log.exception("unhandled error in %s %s", method, path)
                if response_started:  # too late to send an error page; let the server drop the connection
                    raise
                status = 500
                body = {"detail": "Internal server error", "request_id": rid}
                await JSONResponse(body, status_code=500)(scope, receive, send_with_id)
        finally:
            ms = round((time.perf_counter() - started) * 1000)
            if status >= 500:
                level = logging.ERROR
            elif path in QUIET_PATHS:
                level = logging.DEBUG
            else:
                level = logging.INFO
            log.log(level, "%s %s -> %d in %dms", method, path, status, ms)
            request_id.reset(token)
