"""Request body size limit.

FastAPI reads and parses the whole body before dependencies like the API-key check run, so
without a limit an unauthenticated client could make the server buffer arbitrarily large bodies.
A full batch (1000 events) is well under 2 MB, so the default 10 MB cap leaves plenty of room.
"""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        # Chunked uploads have no Content-Length, so also count bytes as they arrive. Past the
        # limit, tell the app the client went away and swallow whatever it answers (FastAPI turns
        # body-read errors into a generic 400), then send the 413 ourselves.
        received = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not too_large:
                await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not too_large:
                raise
        if too_large:
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps(
            {"error": "too_large", "message": f"Request body exceeds {self.max_bytes} bytes"}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"connection", b"close")],
            }
        )
        await send({"type": "http.response.body", "body": body})
