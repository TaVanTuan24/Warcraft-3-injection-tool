"""Shared helpers and exception types."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path


MANAGED_TEMP_PREFIXES: tuple[str, ...] = (
    "war3map_load_",
    "war3map_script_",
    "war3campaign_load_",
    "war3campaign_map_build_",
    "mpq_list_",
    "mpq_extract_single_",
    "mpq_add_",
    "mpq_delete_",
)


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


def create_temp_workspace(prefix: str, logger: logging.Logger) -> Path:
    """Create and log a managed temporary workspace directory."""
    workspace_root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    logger.info("Created temp workspace: %s", workspace_root)
    return workspace_root


class TempResourceTracker:
    """Track tool-managed temp files and directories for guaranteed cleanup."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._paths: list[tuple[Path, str]] = []

    def create_temp_dir(self, prefix: str, label: str = "temp workspace") -> Path:
        """Create and register a managed temporary directory."""
        path = create_temp_workspace(prefix=prefix, logger=self._logger)
        self.register_temp_dir(path, label=label)
        return path

    def register_temp_dir(self, path: Path, label: str = "temp workspace") -> Path:
        """Register a managed temporary directory."""
        return self._register_path(path, label=label)

    def register_temp_file(self, path: Path, label: str = "temp file") -> Path:
        """Register a managed temporary file."""
        return self._register_path(path, label=label)

    def cleanup_all(self, keep: bool = False) -> None:
        """Clean up all tracked temp resources."""
        if not self._paths:
            self._logger.info("Temp cleanup complete.")
            return

        for path, label in reversed(self._paths):
            cleanup_temp_path(path=path, keep=keep, logger=self._logger, label=label)
        self._paths.clear()
        self._logger.info("Temp cleanup complete.")

    def _register_path(self, path: Path, label: str) -> Path:
        resolved_path = path.expanduser().resolve()
        if not is_managed_temp_path(resolved_path):
            raise ValueError(f"Refusing to register unmanaged temp path: {resolved_path}")
        self._paths.append((resolved_path, label))
        return resolved_path


def cleanup_workspace(workspace_root: Path, keep: bool, logger: logging.Logger) -> None:
    """Delete or retain the temporary workspace."""
    cleanup_temp_path(workspace_root, keep=keep, logger=logger, label="temp workspace")


def cleanup_temp_path(
    path: Path,
    keep: bool,
    logger: logging.Logger,
    label: str = "temp path",
) -> None:
    """Delete or retain a managed temporary file or directory."""
    resolved_path = path.expanduser().resolve()
    if keep:
        logger.info("Keeping %s: %s", label, resolved_path)
        return

    if not resolved_path.exists():
        return

    if not is_managed_temp_path(resolved_path):
        logger.warning("Refusing to remove unmanaged %s: %s", label, resolved_path)
        return

    logger.info("Cleaning %s: %s", label, resolved_path)
    try:
        if resolved_path.is_dir():
            shutil.rmtree(resolved_path)
        else:
            resolved_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to remove %s: %s (%s)", label, resolved_path, exc)
        return

    logger.info("%s removed successfully: %s", label.capitalize(), resolved_path)


def is_managed_temp_path(path: Path) -> bool:
    """Return whether a path belongs to a tool-managed temp location."""
    resolved_path = path.expanduser().resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        relative_parts = resolved_path.relative_to(temp_root).parts
    except ValueError:
        return False

    return any(
        any(part.startswith(prefix) for prefix in MANAGED_TEMP_PREFIXES)
        for part in relative_parts
    )


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
