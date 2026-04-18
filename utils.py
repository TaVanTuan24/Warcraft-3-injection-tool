"""Shared helpers and exception types."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path


class ToolError(Exception):
    """Base exception for expected tool failures."""


class ConfigValidationError(ToolError):
    """Raised when patch configuration is invalid."""


class ArchiveProcessingError(ToolError):
    """Raised when the MPQ archive cannot be processed safely."""


class JassPatchError(ToolError):
    """Raised when JASS patching cannot be completed safely."""


def configure_logging(verbose: bool) -> None:
    """Configure application logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def ensure_output_target_is_safe(
    input_path: Path,
    output_path: Path,
    overwrite: bool,
) -> None:
    """Validate output path behavior and overwrite rules."""
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if output_path.exists() and not overwrite:
        raise ValueError(
            f"Output file already exists: {output_path}. Use --overwrite to replace it."
        )

    if input_path.resolve() == output_path.resolve() and not overwrite:
        raise ValueError(
            "Refusing to overwrite the original map. Use --overwrite for explicit in-place output."
        )


def cleanup_workspace(workspace_root: Path, keep: bool, logger: logging.Logger) -> None:
    """Delete or retain the temporary workspace."""
    if keep:
        logger.info("Keeping temporary workspace: %s", workspace_root)
        return

    if workspace_root.exists():
        shutil.rmtree(workspace_root, ignore_errors=True)
        logger.debug("Removed temporary workspace: %s", workspace_root)


def detect_newline(text: str) -> str:
    """Detect the dominant newline in a text blob."""
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def normalize_for_match(text: str) -> str:
    """Normalize text for exact-match style comparisons with stable whitespace handling."""
    return " ".join(text.strip().lower().split())


def to_archive_path(file_path: Path, workspace_root: Path) -> str:
    """Convert a workspace file path into a normalized archive-relative path."""
    resolved_root = workspace_root.resolve()
    resolved_file = file_path.resolve()

    try:
        relative_path = resolved_file.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"Source path is not inside the workspace root: {resolved_file}"
        ) from exc

    archive_path = relative_path.as_posix().strip()
    while archive_path.startswith("./"):
        archive_path = archive_path[2:]
    while archive_path.startswith(".\\"):
        archive_path = archive_path[2:]

    if archive_path in {"", ".", "./", ".\\"}:
        raise ValueError(
            f"Computed invalid archive path '{archive_path or '<empty>'}' for source file {resolved_file}"
        )

    return archive_path
