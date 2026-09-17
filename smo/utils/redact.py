"""
Secret redaction for logs and traces (REC-007).
"""
import logging
import re
from typing import Any

# Patterns to redact (all secrets we care about)
REDACT_PATTERNS = [
    (r"SMO_MASTER_KEY\s*=\s*[^,\n]+", "[REDACTED]"),
    (r"MCP_API_KEY_[^\s]+", "[MCP_KEY_REDACTED]"),
    (r"CLIENT_SECRET\s*=\s*[^,\n]+", "[CLIENT_SECRET_REDACTED]"),
    (r"TOKEN\s*=\s*[^,\n]+", "[TOKEN_REDACTED]"),
    (r"ACCESS_TOKEN\s*=\s*[^,\n]+", "[ACCESS_TOKEN_REDACTED]"),
    (r"Bearer\s+[^\s]+", "[BEARER_REDACTED]"),
    (r"Authorization\s+Bearer\s+[^\s]+", "[AUTH_REDACTED]"),
    (r"private_key\s*=\s*[^,\n]+", "[PRIVATE_KEY_REDACTED]"),
    (r"secret\s*=\s*[^,\n]+", "[SECRET_REDACTED]"),
]

# Fields to redact from logs
REDACT_FIELDS = {"SMO_MASTER_KEY", "MCP_API_KEY", "CLIENT_SECRET", "TOKEN", "ACCESS_TOKEN"}


def redact_passwords(text: str) -> str:
    """
    Redact secrets in text (logs, traces, errors).
    Returns new string with secrets replaced by placeholders.
    """
    result = text
    for pattern, replacement in REDACT_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    """
    Redact sensitive fields from a dictionary.
    Used when logging or returning structures that might contain secrets.
    """
    return {k: "[REDACTED]" if k in REDACT_FIELDS else v for k, v in d.items()}


# Example: Logging filter
class SecretRedactionFilter(logging.Filter):
    """Logging filter that redacts secrets from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg"):
            if isinstance(record.msg, str):
                record.msg = redact_passwords(record.msg)
        if hasattr(record, "args"):
            if isinstance(record.args, tuple):
                record.args = tuple(redact_passwords(str(arg)) if isinstance(arg, str) else arg for arg in record.args)
        return True
