"""MCP over HTTP at /mcp (FR-14, FR-15): bearer key selects the agent."""
import json

from fastapi.testclient import TestClient

from smo.server.app import create_app

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _json_from_response(response):
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"no JSON-RPC payload in {response.text!r}")


def test_mcp_rejects_missing_or_wrong_key(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    monkeypatch.setenv("MCP_API_KEY_HERMES", "hermes-secret")

    with TestClient(create_app()) as client:
        missing = client.post("/mcp", json=INIT, headers=HEADERS)
        wrong = client.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer nope"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"


def test_mcp_initialize_with_agent_key(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    monkeypatch.setenv("MCP_API_KEY_HERMES", "hermes-secret")

    with TestClient(create_app(), base_url="http://127.0.0.1:8023") as client:
        response = client.post(
            "/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer hermes-secret"}
        )

    assert response.status_code == 200
    payload = _json_from_response(response)
    assert payload["result"]["serverInfo"]["name"] == "smo"


def test_mcp_rejects_unknown_host_header(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    monkeypatch.setenv("MCP_API_KEY_HERMES", "hermes-secret")

    with TestClient(create_app(), base_url="http://attacker.example") as client:
        response = client.post(
            "/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer hermes-secret"}
        )

    assert response.status_code == 421
