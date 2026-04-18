"""Helpers for detecting supported Warcraft 3 archive input types."""

from __future__ import annotations

from pathlib import Path

from models import InputType
from utils import ArchiveProcessingError


_INPUT_TYPE_BY_SUFFIX = {
    ".w3x": InputType.MAP_W3X,
    ".w3m": InputType.MAP_W3M,
    ".w3n": InputType.CAMPAIGN_W3N,
}


def detect_input_type(path: Path) -> InputType:
    """Detect the supported Warcraft 3 archive type for a path."""
    suffix = path.suffix.lower()
    try:
        return _INPUT_TYPE_BY_SUFFIX[suffix]
    except KeyError as exc:
        raise ArchiveProcessingError(
            f"Unsupported input file type '{path.suffix}'. Supported types: .w3x, .w3m, .w3n."
        ) from exc


def default_output_path(path: Path) -> Path:
    """Create the default patched output path for a supported input archive."""
    input_type = detect_input_type(path)
    return path.with_name(f"{path.stem}_patched{input_type.suffix}")
