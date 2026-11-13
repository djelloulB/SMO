"""Per-agent API keys and scopes for the MCP control plane (FR-15).

Keys are read from ``MCP_API_KEY_<AGENT>`` environment variables. The agent name
selects the scopes (see ``SCOPE_BY_AGENT``); unknown agents get ``read`` only.
Audit entries use the agent name, never the key itself (REC-008).
"""
import hmac
import os
from dataclasses import dataclass
from typing import Mapping, Optional

KEY_PREFIX = "MCP_API_KEY_"
STDIO_KEY_ENV = "SMO_MCP_API_KEY"

SCOPE_BY_AGENT: dict[str, frozenset[str]] = {
    "HERMES": frozenset({"read", "content"}),
    "ADMIN": frozenset({"read", "content", "control", "admin"}),
}
DEFAULT_SCOPES = frozenset({"read"})


@dataclass(frozen=True)
class Identity:
    actor_id: str
    scopes: frozenset[str]


def load_agent_keys(environ: Optional[Mapping[str, str]] = None) -> dict[str, Identity]:
    env = os.environ if environ is None else environ
    keys: dict[str, Identity] = {}
    for name, value in env.items():
        if not name.upper().startswith(KEY_PREFIX) or not value:
            continue
        agent = name[len(KEY_PREFIX):].upper()
        keys[value] = Identity(
            actor_id=f"agent:{agent.lower()}",
            scopes=SCOPE_BY_AGENT.get(agent, DEFAULT_SCOPES),
        )
    return keys


def authenticate(token: Optional[str], environ: Optional[Mapping[str, str]] = None) -> Optional[Identity]:
    if not token:
        return None
    match: Optional[Identity] = None
    for key, identity in load_agent_keys(environ).items():
        # Compare every key so timing does not reveal which one matched.
        if hmac.compare_digest(key.encode("utf-8"), token.encode("utf-8")):
            match = identity
    return match


def authenticate_stdio(environ: Optional[Mapping[str, str]] = None) -> Optional[Identity]:
    env = os.environ if environ is None else environ
    return authenticate(env.get(STDIO_KEY_ENV), env)
