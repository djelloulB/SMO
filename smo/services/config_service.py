"""Runtime configuration for mutable keys (REC-002 layer enforced).

Values are validated by ``MutableConfig`` (the immutable layer), then stored in
SQLite so every process reads the same value. Immutable controls such as
``dry_run``, ``human_gate``, ``budget_per_day_usd`` and the MCP scopes are refused.
"""
import json
from typing import Any, Optional

from ..config.security import MutableConfig, mutable_config
from ..memory.sqlite_store import RuntimeConfig, SQLiteStore


class ConfigService:
    def __init__(self, store: Optional[SQLiteStore] = None):
        self.store = store or SQLiteStore()

    def get(self, key: str, default: Any = None) -> Any:
        with self.store.get_session() as session:
            row = session.get(RuntimeConfig, key)
            return json.loads(row.value) if row is not None else default

    def set(self, key: str, value: Any) -> None:
        # Validate on a throwaway instance first: a refused value must not reach storage.
        MutableConfig().set(key, value)
        serialized = json.dumps(value)
        with self.store.get_session() as session:
            row = session.get(RuntimeConfig, key)
            if row is None:
                session.add(RuntimeConfig(key=key, value=serialized))
            else:
                row.value = serialized
            session.commit()
        mutable_config.set(key, value)

    def all(self) -> dict[str, Any]:
        with self.store.get_session() as session:
            return {row.key: json.loads(row.value) for row in session.query(RuntimeConfig).all()}
