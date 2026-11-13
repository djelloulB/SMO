"""MCP over HTTP at ``/mcp`` (FR-14), with one FastMCP instance per agent key.

A request must carry ``Authorization: Bearer <key>``. The key selects the agent,
and therefore the scopes, before any MCP message is read. Each agent gets its own
server instance, so one agent cannot reach another agent's tools.
"""
import hmac
import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Callable, Iterable, Optional

from mcp.server.fastmcp import FastMCP

from .auth import Identity, load_agent_keys

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"


class MCPHttpGateway:
    def __init__(
        self,
        identities: dict[str, Identity],
        build: Callable[[Identity], FastMCP],
    ):
        self._routes: list[tuple[bytes, Identity, object]] = []
        self._servers: list[FastMCP] = []
        for key, identity in identities.items():
            server = build(identity)
            self._servers.append(server)
            self._routes.append((key.encode("utf-8"), identity, server.streamable_http_app()))

    @classmethod
    def from_environment(cls, build: Callable[[Identity], FastMCP], environ=None) -> "MCPHttpGateway":
        return cls(load_agent_keys(environ), build)

    @property
    def agents(self) -> list[str]:
        return [identity.actor_id for _, identity, _ in self._routes]

    @asynccontextmanager
    async def lifespan(self):
        """Run each server's session manager. Required because mounted sub-apps do not get their own lifespan."""
        async with AsyncExitStack() as stack:
            for server in self._servers:
                await stack.enter_async_context(server.session_manager.run())
            yield

    def _match(self, token: Optional[str]):
        found = None
        for key, identity, app in self._routes:
            # Compare every key so the timing does not reveal which one matched.
            if token is not None and hmac.compare_digest(key, token.encode("utf-8")):
                found = (identity, app)
        return found

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        token = _bearer_token(scope.get("headers", []))
        match = self._match(token)
        if match is None:
            await _send_json(send, 401, {"error": "invalid or missing MCP API key"}, [(b"www-authenticate", b"Bearer")])
            return
        _, app = match
        await app(scope, receive, send)


def _bearer_token(headers: Iterable[tuple[bytes, bytes]]) -> Optional[str]:
    for name, value in headers:
        if name.lower() == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    return None


async def _send_json(send, status: int, body: dict, extra_headers=()) -> None:
    payload = json.dumps(body).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})
