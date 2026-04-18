"""Direct script loading services for Warcraft 3 map archives."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from models import InputType, MapSourceContext
from services.patch_support import resolve_patch_backend, validate_patch_backend_support
from services.input_detector import detect_input_type
from services.validator import validate_loaded_map_source
from utils import ArchiveProcessingError, TempResourceTracker, cleanup_workspace


def load_map_source(
    input_war3_archive: Path,
    external_listfiles: Sequence[Path] | None = None,
    progress_callback=None,
    log_callback=None,
) -> MapSourceContext:
    """Load the target map script from an archive without extracting the full map."""
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)
    resolved_listfiles = tuple(Path(path).expanduser().resolve() for path in (external_listfiles or ()))

    input_type = detect_input_type(input_war3_archive)
    if not input_type.is_map:
        raise ArchiveProcessingError(
            f"Unsupported map input type '{input_war3_archive.suffix}'. Only .w3x and .w3m maps can be loaded as maps."
        )

    log("INFO", "Loading map archive.")
    handler, capabilities = resolve_patch_backend(
        external_listfiles=resolved_listfiles,
        log_callback=log,
    )
    backend_validation = validate_patch_backend_support(handler, capabilities)
    if not backend_validation.is_valid:
        raise ArchiveProcessingError(" ".join(backend_validation.issues))

    tracker = TempResourceTracker(_TempLogger(log))
    workspace_root = tracker.create_temp_dir("war3map_script_")
    extracted_dir = workspace_root / "script_contents"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    progress("detecting script path")
    try:
        if resolved_listfiles:
            log("INFO", f"Using {len(resolved_listfiles)} external listfile(s) during archive access.")
        script_entry_path = handler.get_script_entry_path(
            archive_path=input_war3_archive,
            external_listfiles=resolved_listfiles,
        )
        log("INFO", f"Detected script path: {script_entry_path}")

        script_path = extracted_dir / Path(script_entry_path)
        progress("reading script")
        handler.extract_file_from_archive_with_listfiles(
            archive_path=input_war3_archive,
            archive_entry_path=script_entry_path,
            output_path=script_path,
            external_listfiles=resolved_listfiles,
        )
        try:
            source_text = script_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ArchiveProcessingError(
                f"Script found but unreadable: {script_entry_path}. {exc}"
            ) from exc

        context = MapSourceContext(
            input_path=input_war3_archive,
            input_type=input_type,
            workspace_root=workspace_root,
            extracted_dir=extracted_dir,
            script_path=script_path,
            script_relative_path=Path(script_entry_path),
            source_text=source_text,
            script_candidates=(Path(script_entry_path),),
            external_listfiles=resolved_listfiles,
            temp_tracker=tracker,
        )
        validation = validate_loaded_map_source(context)
        if not validation.is_valid:
            raise ArchiveProcessingError(" ".join(validation.issues))
        for warning in validation.warnings:
            log("WARNING", warning)
        return context
    except Exception:
        tracker.cleanup_all(keep=False)
        raise


def dispose_map_source(context: MapSourceContext, keep: bool = False, log_callback=None) -> None:
    """Clean up the temporary script workspace."""
    log = log_callback or (lambda _severity, _message: None)
    if context.temp_tracker is not None and hasattr(context.temp_tracker, "cleanup_all"):
        context.temp_tracker.cleanup_all(keep=keep)
        return
    cleanup_workspace(context.workspace_root, keep=keep, logger=_TempLogger(log))


class _TempLogger:
    def __init__(self, callback) -> None:
        self._callback = callback

    def info(self, message: str, *args: object) -> None:
        self._callback("INFO", message % args if args else message)

    def debug(self, message: str, *args: object) -> None:
        self._callback("INFO", message % args if args else message)

    def warning(self, message: str, *args: object) -> None:
        self._callback("WARNING", message % args if args else message)
