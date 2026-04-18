"""Archive rebuild services."""

from __future__ import annotations

from pathlib import Path

from models import BuildResult
from mpq_handler import MpqHandler


def build_archive(source_dir: Path, output_path: Path, log_callback=None) -> BuildResult:
    """Rebuild a Warcraft 3 archive from an extracted directory."""
    log = log_callback or (lambda _severity, _message: None)
    log("INFO", f"Rebuilding archive: {output_path}")
    handler = MpqHandler.auto_detect()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    handler.rebuild_archive(source_dir=source_dir, output_path=output_path)
    return BuildResult(output_path=output_path)


def build_map(temp_dir: Path, output_path: Path, log_callback=None) -> BuildResult:
    """Backwards-compatible wrapper for rebuilding maps."""
    return build_archive(temp_dir, output_path, log_callback=log_callback)


def build_campaign(temp_dir: Path, output_path: Path, log_callback=None) -> BuildResult:
    """Rebuild a campaign archive from an extracted directory."""
    return build_archive(temp_dir, output_path, log_callback=log_callback)
