"""Map extraction and loading services."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from models import InputType, MapSourceContext, MpqBackendType
from mpq_handler import MpqHandler
from services.input_detector import detect_input_type
from services.validator import validate_loaded_map_source
from utils import ArchiveProcessingError, cleanup_workspace


KNOWN_SCRIPT_RELATIVE_PATHS = (
    Path("war3map.j"),
    Path("scripts") / "war3map.j",
)


@dataclass(frozen=True, slots=True)
class MapScriptDiscovery:
    """Resolved map script selection details."""

    selected_path: Path
    selected_relative_path: Path
    candidate_paths: tuple[Path, ...]
    warning: str | None = None


def find_map_script(extracted_root: Path, log_callback=None) -> Path:
    """Locate the most likely Warcraft 3 map script inside an extracted workspace."""
    return _discover_map_script(extracted_root, log_callback=log_callback).selected_path


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
        discovery = _discover_map_script(extracted_dir, log_callback=log)
        try:
            source_text = discovery.selected_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ArchiveProcessingError(
                f"Script found but unreadable: {discovery.selected_relative_path.as_posix()}. {exc}"
            ) from exc

        context = MapSourceContext(
            input_path=input_war3_archive,
            input_type=input_type,
            workspace_root=workspace_root,
            extracted_dir=extracted_dir,
            script_path=discovery.selected_path,
            script_relative_path=discovery.selected_relative_path,
            source_text=source_text,
            script_candidates=discovery.candidate_paths,
            script_discovery_warning=discovery.warning,
            external_listfiles=resolved_listfiles,
        )
        validation = validate_loaded_map_source(context)
        if not validation.is_valid:
            raise ArchiveProcessingError(" ".join(validation.issues))
        for warning in validation.warnings:
            log("WARNING", warning)
        return context
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


def _discover_map_script(extracted_root: Path, log_callback=None) -> MapScriptDiscovery:
    """Resolve the most likely war3map.j candidate inside an extracted workspace."""
    log = log_callback or (lambda _severity, _message: None)

    if not extracted_root.exists():
        raise ArchiveProcessingError(f"Extracted workspace does not exist: {extracted_root}")
    if not extracted_root.is_dir():
        raise ArchiveProcessingError(f"Extracted workspace is not a directory: {extracted_root}")

    log("INFO", f"Searching for war3map.j in extracted workspace: {extracted_root}")

    found_by_resolved_path: dict[Path, Path] = {}
    for relative_path in KNOWN_SCRIPT_RELATIVE_PATHS:
        candidate_path = (extracted_root / relative_path).resolve()
        if candidate_path.is_file():
            _register_candidate(extracted_root, candidate_path, found_by_resolved_path, log)

    for candidate_path in extracted_root.rglob("*"):
        if not candidate_path.is_file() or candidate_path.name != "war3map.j":
            continue
        _register_candidate(extracted_root, candidate_path.resolve(), found_by_resolved_path, log)

    if not found_by_resolved_path:
        log("ERROR", "No script found: war3map.j is missing from the extracted workspace.")
        message = "Script not found: could not locate 'war3map.j' in extracted map contents."
        if any(extracted_root.iterdir()):
            message += " Normal extraction completed, but the map script was not present."
        else:
            message += " The extracted workspace is empty."
        raise ArchiveProcessingError(message)

    candidates = tuple(
        sorted(
            found_by_resolved_path,
            key=lambda path: _candidate_sort_key(extracted_root, path),
        )
    )
    if len(candidates) > 1:
        candidate_list = ", ".join(
            _relative_script_path(extracted_root, path).as_posix() for path in candidates
        )
        log("WARNING", f"Multiple script candidates detected: {candidate_list}")

    selected_path = candidates[0]
    selected_relative_path = _relative_script_path(extracted_root, selected_path)
    ambiguity_group = [
        path
        for path in candidates
        if _candidate_rank(extracted_root, path) == _candidate_rank(extracted_root, selected_path)
    ]
    if len(ambiguity_group) > 1 and selected_relative_path not in KNOWN_SCRIPT_RELATIVE_PATHS:
        ambiguous_paths = ", ".join(
            _relative_script_path(extracted_root, path).as_posix() for path in ambiguity_group
        )
        log("ERROR", f"Multiple script candidates are ambiguous: {ambiguous_paths}")
        raise ArchiveProcessingError(
            "Multiple matches ambiguous: found equally likely 'war3map.j' files at "
            f"{ambiguous_paths}."
        )

    log("INFO", f"Selected script: {selected_relative_path.as_posix()}")

    warning = None
    if len(candidates) > 1:
        warning = (
            "Multiple script candidates detected; selected "
            f"'{selected_relative_path.as_posix()}' using known-path and shortest-path priority."
        )

    return MapScriptDiscovery(
        selected_path=selected_path,
        selected_relative_path=selected_relative_path,
        candidate_paths=candidates,
        warning=warning,
    )


def _register_candidate(
    extracted_root: Path,
    candidate_path: Path,
    found_by_resolved_path: dict[Path, Path],
    log_callback,
) -> None:
    if candidate_path in found_by_resolved_path:
        return
    found_by_resolved_path[candidate_path] = candidate_path
    log_callback(
        "INFO",
        f"Found candidate: {_relative_script_path(extracted_root, candidate_path).as_posix()}",
    )


def _candidate_sort_key(extracted_root: Path, candidate_path: Path) -> tuple[int, int, str]:
    relative_path = _relative_script_path(extracted_root, candidate_path)
    return (
        _known_path_priority(relative_path),
        len(relative_path.parts),
        relative_path.as_posix(),
    )


def _candidate_rank(extracted_root: Path, candidate_path: Path) -> tuple[int, int]:
    relative_path = _relative_script_path(extracted_root, candidate_path)
    return (_known_path_priority(relative_path), len(relative_path.parts))


def _known_path_priority(relative_path: Path) -> int:
    try:
        return KNOWN_SCRIPT_RELATIVE_PATHS.index(relative_path)
    except ValueError:
        return len(KNOWN_SCRIPT_RELATIVE_PATHS)


def _relative_script_path(extracted_root: Path, candidate_path: Path) -> Path:
    return candidate_path.relative_to(extracted_root)
