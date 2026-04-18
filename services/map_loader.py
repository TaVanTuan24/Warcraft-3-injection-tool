"""Map extraction and loading services."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Sequence

from models import InputType, MapSourceContext, MpqBackendType
from mpq_handler import MpqHandler
from services.input_detector import detect_input_type
from services.validator import validate_map_source_text
from utils import ArchiveProcessingError, cleanup_workspace


def load_map_source(
    input_war3_archive: Path,
    external_listfiles: Sequence[Path] | None = None,
    progress_callback=None,
    log_callback=None,
) -> MapSourceContext:
    """Extract a readable map archive and load its war3map.j source."""
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)
    resolved_listfiles = tuple(Path(path).expanduser().resolve() for path in (external_listfiles or ()))

    input_type = detect_input_type(input_war3_archive)
    if not input_type.is_map:
        raise ArchiveProcessingError(
            f"Unsupported map input type '{input_war3_archive.suffix}'. Only .w3x and .w3m maps can be loaded as maps."
        )

    log("INFO", "Detecting MPQ backend.")
    handler = MpqHandler.auto_detect(
        preferred_backend_type=MpqBackendType.MPQEDITOR if resolved_listfiles else None
    )
    workspace_root = Path(tempfile.mkdtemp(prefix="war3map_load_")).resolve()
    extracted_dir = workspace_root / "map_contents"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    progress("loading map")
    log("INFO", f"Extracting map archive: {input_war3_archive}")
    try:
        if resolved_listfiles:
            log("INFO", f"Using {len(resolved_listfiles)} external listfile(s) during extraction.")
        handler.extract_archive(
            input_path=input_war3_archive,
            destination_dir=extracted_dir,
            external_listfiles=resolved_listfiles,
        )
        war3map_j_path = extracted_dir / "war3map.j"
        if not war3map_j_path.is_file():
            message = "Missing required file 'war3map.j' in extracted map contents."
            if resolved_listfiles:
                message += " The selected listfile may be incomplete for this protected map."
            else:
                message += " Try adding a listfile if the map is protected or obfuscated."
            raise ArchiveProcessingError(message)
        source_text = war3map_j_path.read_text(encoding="utf-8")
        issues = validate_map_source_text(source_text)
        if issues:
            raise ArchiveProcessingError(" ".join(issues))
        return MapSourceContext(
            input_path=input_war3_archive,
            input_type=input_type,
            workspace_root=workspace_root,
            extracted_dir=extracted_dir,
            war3map_j_path=war3map_j_path,
            source_text=source_text,
            external_listfiles=resolved_listfiles,
        )
    except Exception:
        cleanup_workspace(workspace_root, keep=False, logger=_TempLogger(log))
        raise


def dispose_map_source(context: MapSourceContext, keep: bool = False, log_callback=None) -> None:
    """Clean up a loaded map workspace."""
    log = log_callback or (lambda _severity, _message: None)
    cleanup_workspace(context.workspace_root, keep=keep, logger=_TempLogger(log))


class _TempLogger:
    def __init__(self, callback) -> None:
        self._callback = callback

    def info(self, message: str, *args: object) -> None:
        self._callback("INFO", message % args if args else message)

    def debug(self, message: str, *args: object) -> None:
        self._callback("INFO", message % args if args else message)
