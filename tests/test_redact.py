"""Secret redaction tests (REC-007)."""
import logging

from smo.utils.redact import SecretRedactionFilter, redact_dict, redact_passwords


def test_redact_passwords_masks_known_secrets():
    text = "SMO_MASTER_KEY=super-secret TOKEN=abc123\nAuthorization: Bearer xyz"

    redacted = redact_passwords(text)

    assert "super-secret" not in redacted
    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert "REDACTED" in redacted


def test_redact_dict_masks_sensitive_fields():
    data = {"SMO_MASTER_KEY": "secret", "TOKEN": "abc", "safe": "value"}

    redacted = redact_dict(data)

    assert redacted == {
        "SMO_MASTER_KEY": "[REDACTED]",
        "TOKEN": "[REDACTED]",
        "safe": "value",
    }


def test_logging_filter_redacts_message_and_args():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="token=%s",
        args=("Bearer abc123",),
        exc_info=None,
    )

    SecretRedactionFilter().filter(record)

    assert "abc123" not in record.getMessage()
