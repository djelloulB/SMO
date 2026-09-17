"""
Path confinement and sanitization (REC-006).
Confines local source ingestion and media filenames.
"""
import os
import re
from pathlib import Path
from typing import Optional


# Base path for local sources (confined per REC-006)
BASE_INPUT_DIR = Path("data/inputs")
BASE_OUTPUT_DIR = Path("data/media")
BASE_DRYRUN_DIR = Path("data/dryrun")

# Allowed characters for filenames (remove path separators and control chars)
SAFE_FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")


def ensure_base_dir() -> None:
    """
    Ensure base directories exist.
    Creates them if they don't exist.
    """
    BASE_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    BASE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    BASE_DRYRUN_DIR.mkdir(parents=True, exist_ok=True)


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent path traversal and control chars.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename with only safe characters.
    """
    # Remove path separators and control chars
    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    sanitized = sanitized.strip("_")

    # Ensure non-empty
    if not sanitized:
        sanitized = "unnamed"

    # Limit length to prevent filesystem issues
    if len(sanitized) > 200:
        sanitized = sanitized[:200]

    return sanitized


def normalize_path(base: Path, user_path: str) -> Path:
    """
    Normalize a user-provided path against a base directory.
    Rejects absolute paths, `..` components, and paths outside base.

    Args:
        base: Base directory (the confinement boundary)
        user_path: Path provided by user (may contain `..`, relative paths)

    Returns:
        Normalized Path inside base.
    """
    # Resolve relative to base
    normalized = (base / user_path).resolve()

    # Ensure it's inside base
    try:
        normalized.relative_to(base)
    except ValueError:
        raise ValueError(
            f"Path '{user_path}' resolved outside base directory '{base}'. "
            f"See SECURITY.md REC-006."
        )

    # Check for traversal (`..` in path)
    if ".." in normalized.parts:
        raise ValueError(
            f"Path traversal detected in '{user_path}'. Only paths relative to '{base}' are allowed."
        )

    return normalized


def get_safe_path(
    base: Path,
    user_path: str,
    sanitize: bool = True,
    allowed_extensions: Optional[list[str]] = None,
) -> Path:
    """
    Get a safe, confined path.

    Args:
        base: Base directory (confinement boundary)
        user_path: User-provided path
        sanitize: If True, sanitize filename
        allowed_extensions: If set, only allow these extensions

    Returns:
        Safe Path inside base.
    """
    ensure_base_dir()

    # Normalize path (rejects traversal)
    normalized = normalize_path(base, user_path)

    if allowed_extensions:
        if normalized.suffix not in allowed_extensions:
            raise ValueError(
                f"File extension '{normalized.suffix}' not allowed. Allowed: {allowed_extensions}"
            )

    if sanitize:
        filename = normalized.name
        safe_name = sanitize_filename(filename)
        if safe_name != filename:
            normalized = normalized.with_name(safe_name)

    return normalized


def ensure_safe_write(filepath: Path, content: bytes, permissions: int = 0o644) -> None:
    """
    Ensure a file can be written safely.
    Creates directories and applies strict permissions.
    """
    ensure_base_dir()
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Write with strict permissions
    with open(filepath, "wb") as f:
        f.write(content)

    # Set file permissions (enforce REC-006)
    os.chmod(filepath, permissions)
