"""Validation services for the trigger injector."""

from __future__ import annotations

import re
from pathlib import Path

from jass_patcher import get_effective_patch_config
from models import (
    CampaignMapEntry,
    InputType,
    MapSourceContext,
    MpqBackendType,
    PatchMode,
    PatchConfig,
    PatchSelection,
    ValidationResult,
)
from mpq_handler import MpqHandler
from services.input_detector import detect_input_type
from utils import ensure_output_target_is_safe


GLOBALS_PATTERN = re.compile(
    r"^[ \t]*globals\b.*?^[ \t]*endglobals\b",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
MAIN_PATTERN = re.compile(
    r"^[ \t]*function[ \t]+main[ \t]+takes[ \t]+nothing[ \t]+returns[ \t]+nothing\b",
    re.IGNORECASE | re.MULTILINE,
)


def validate_map_source_text(source_text: str) -> list[str]:
    """Validate the required structures inside a target map source file."""
    issues: list[str] = []
    if not GLOBALS_PATTERN.search(source_text):
        issues.append("Missing required 'globals ... endglobals' block.")
    if not MAIN_PATTERN.search(source_text):
        issues.append("Missing required 'function main takes nothing returns nothing' declaration.")
    return issues


def validate_loaded_map_source(map_source: MapSourceContext) -> ValidationResult:
    """Validate the extracted workspace and resolved script path for a loaded map."""
    issues: list[str] = []
    warnings: list[str] = []

    if not map_source.extracted_dir.exists():
        issues.append(f"Extracted workspace does not exist: {map_source.extracted_dir}")
        return ValidationResult(is_valid=False, issues=issues, warnings=warnings)
    if not map_source.extracted_dir.is_dir():
        issues.append(f"Extracted workspace is not a directory: {map_source.extracted_dir}")
        return ValidationResult(is_valid=False, issues=issues, warnings=warnings)

    script_relative = map_source.script_relative_path.as_posix()
    if not map_source.script_path.exists() or not map_source.script_path.is_file():
        issues.append(
            f"Script not found: detected path '{script_relative}' is missing from the extracted workspace."
        )
        return ValidationResult(is_valid=False, issues=issues, warnings=warnings)

    try:
        source_text = map_source.script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        issues.append(f"Script found but unreadable: {script_relative}. {exc}")
        return ValidationResult(is_valid=False, issues=issues, warnings=warnings)

    for issue in validate_map_source_text(source_text):
        issues.append(f"Invalid JASS structure in detected script '{script_relative}': {issue}")

    if map_source.script_discovery_warning:
        warnings.append(map_source.script_discovery_warning)

    return ValidationResult(is_valid=not issues, issues=issues, warnings=warnings)


def validate_before_inject(
    input_map: Path | None,
    output_map: Path | None,
    selected_patch: PatchSelection,
    overwrite: bool = False,
    patch_mode: PatchMode = PatchMode.AUTO,
    map_source: MapSourceContext | None = None,
    source_text: str | None = None,
    campaign_maps: list[CampaignMapEntry] | None = None,
    stop_on_first_error: bool = False,
) -> ValidationResult:
    """Validate the current job before injection begins."""
    issues: list[str] = []
    warnings: list[str] = []

    input_type: InputType | None = None
    if input_map is None:
        issues.append("Input archive path is required.")
    elif not input_map.is_file():
        issues.append(f"Input file not found: {input_map}")
    else:
        try:
            input_type = detect_input_type(input_map)
        except Exception as exc:
            issues.append(str(exc))

    if output_map is None:
        issues.append("Output archive path is required.")
    elif input_type is not None and output_map.suffix.lower() != input_type.suffix:
        issues.append(
            f"Output file must use the {input_type.suffix} extension for this input type."
        )

    if input_map and output_map and input_map.exists():
        try:
            ensure_output_target_is_safe(input_map, output_map, overwrite)
        except (FileNotFoundError, ValueError) as exc:
            issues.append(str(exc))

    if not (
        selected_patch.enabled_globals()
        or selected_patch.enabled_functions()
        or selected_patch.enabled_main_calls()
    ):
        issues.append("At least one imported trigger item must be enabled before injection.")

    issues.extend(_find_duplicate_issues(selected_patch))

    if input_type is None:
        return ValidationResult(is_valid=not issues, issues=issues, warnings=warnings)

    if input_type.is_map:
        if map_source is not None:
            loaded_validation = validate_loaded_map_source(map_source)
            issues.extend(loaded_validation.issues)
            warnings.extend(loaded_validation.warnings)
            source_text = map_source.source_text
            if source_text is not None:
                warnings.extend(_effective_warnings(source_text, selected_patch))
            fast_replace_validation = _validate_requested_fast_replace(
                patch_mode=patch_mode,
                input_map=input_map,
                map_source=map_source,
            )
            issues.extend(fast_replace_validation.issues)
            warnings.extend(fast_replace_validation.warnings)
        elif source_text is not None:
            issues.extend(validate_map_source_text(source_text))
            warnings.extend(_effective_warnings(source_text, selected_patch))
    else:
        campaign_entries = campaign_maps or []
        if not campaign_entries:
            issues.append("Campaign scan results are required before campaign injection.")
            return ValidationResult(is_valid=not issues, issues=issues, warnings=warnings)

        selected_entries = [entry for entry in campaign_entries if entry.selected]
        if not selected_entries:
            issues.append("At least one campaign map must be selected before injection.")
        for entry in selected_entries:
            if entry.patchable:
                continue
            if stop_on_first_error:
                issues.append(
                    f"Selected campaign map is unreadable or unpatchable: {entry.archive_path}. {entry.message}"
                )
            else:
                warnings.append(
                    f"Selected campaign map will be skipped if it fails: {entry.archive_path}. {entry.message}"
                )

    return ValidationResult(is_valid=not issues, issues=issues, warnings=warnings)


def _effective_warnings(source_text: str, selected_patch: PatchSelection) -> list[str]:
    warnings: list[str] = []
    try:
        effective = get_effective_patch_config(
            source_text,
            PatchConfig(
                globals_to_add=[entry.text for entry in selected_patch.enabled_globals()],
                functions_to_add=[entry.text for entry in selected_patch.enabled_functions()],
                init_calls=[entry.text for entry in selected_patch.enabled_main_calls()],
            ),
        )
        if not (effective.globals_to_add or effective.functions_to_add or effective.init_calls):
            warnings.append(
                "No new insertions are pending. The selected target may already contain all enabled entries."
            )
    except Exception as exc:
        return [str(exc)]
    return warnings


def _find_duplicate_issues(selected_patch: PatchSelection) -> list[str]:
    issues: list[str] = []

    globals_seen: set[str] = set()
    globals_dupes: set[str] = set()
    for entry in selected_patch.enabled_globals():
        candidate = entry.text.strip()
        if candidate in globals_seen:
            globals_dupes.add(candidate)
        globals_seen.add(candidate)
    if globals_dupes:
        issues.append("Duplicate enabled globals: " + ", ".join(sorted(globals_dupes)))

    functions_seen: set[str] = set()
    functions_dupes: set[str] = set()
    for entry in selected_patch.enabled_functions():
        candidate = entry.signature
        if candidate in functions_seen:
            functions_dupes.add(entry.name or entry.signature)
        functions_seen.add(candidate)
    if functions_dupes:
        issues.append("Duplicate enabled functions: " + ", ".join(sorted(functions_dupes)))

    calls_seen: set[str] = set()
    calls_dupes: set[str] = set()
    for entry in selected_patch.enabled_main_calls():
        candidate = entry.text.strip()
        if candidate in calls_seen:
            calls_dupes.add(candidate)
        calls_seen.add(candidate)
    if calls_dupes:
        issues.append("Duplicate enabled main calls: " + ", ".join(sorted(calls_dupes)))

    return issues


def validate_fast_replace_preconditions(
    input_map: Path,
    map_source: MapSourceContext,
    patched_script_path: Path,
    handler: MpqHandler,
) -> ValidationResult:
    """Validate the fast archive-replace workflow for one readable map."""
    issues: list[str] = []
    warnings: list[str] = []

    if not input_map.is_file():
        issues.append(f"Input archive path is required for fast replace: {input_map}")

    if not handler.supports_fast_replace():
        issues.append(
            f"Backend '{handler.backend.name}' does not support fast replace "
            "(requires list, delete, add, and replace entry operations)."
        )
        return ValidationResult(is_valid=False, issues=issues, warnings=warnings)

    script_entry_path = map_source.script_relative_path.as_posix()
    if not script_entry_path:
        issues.append("Fast replace requires a detected script path.")

    if not patched_script_path.is_file():
        issues.append(f"Patched script file does not exist: {patched_script_path}")

    if issues:
        return ValidationResult(is_valid=False, issues=issues, warnings=warnings)

    archive_entries = set(handler.list_archive_entries(input_map))
    if script_entry_path not in archive_entries:
        issues.append(
            f"Detected script entry was not found inside the archive: {script_entry_path}"
        )

    return ValidationResult(is_valid=not issues, issues=issues, warnings=warnings)


def _validate_requested_fast_replace(
    patch_mode: PatchMode,
    input_map: Path | None,
    map_source: MapSourceContext,
) -> ValidationResult:
    """Validate or warn about fast replace according to the selected patch mode."""
    if input_map is None or not input_map.is_file():
        return ValidationResult(is_valid=True, issues=[], warnings=[])

    if patch_mode is PatchMode.FULL_REBUILD:
        return ValidationResult(is_valid=True, issues=[], warnings=[])

    try:
        handler = MpqHandler.auto_detect(preferred_backend_type=MpqBackendType.MPQEDITOR)
    except Exception as exc:
        if patch_mode is PatchMode.FAST_REPLACE:
            return ValidationResult(is_valid=False, issues=[str(exc)], warnings=[])
        return ValidationResult(
            is_valid=True,
            issues=[],
            warnings=[
                f"Fast replace is unavailable with the current backend. Auto will use full rebuild. {exc}"
            ],
        )

    validation = validate_fast_replace_preconditions(
        input_map=input_map,
        map_source=map_source,
        patched_script_path=map_source.script_path,
        handler=handler,
    )
    if patch_mode is PatchMode.FAST_REPLACE:
        return validation
    if validation.is_valid:
        return ValidationResult(is_valid=True, issues=[], warnings=[])
    return ValidationResult(
        is_valid=True,
        issues=[],
        warnings=[
            "Fast replace is unavailable for this map. Auto will use full rebuild. "
            + " ".join(validation.issues)
        ],
    )
