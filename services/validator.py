"""Validation services for the direct-replace trigger injector."""

from __future__ import annotations

import re
from pathlib import Path

from jass_patcher import get_effective_patch_config
from models import (
    CampaignMapEntry,
    InputType,
    MapSourceContext,
    PatchConfig,
    PatchSelection,
    ValidationResult,
)
from mpq_handler import MpqHandler
from services.input_detector import detect_input_type
from services.patch_support import (
    describe_backend,
    format_patch_capabilities,
    get_patch_capabilities,
    resolve_patch_backend,
    validate_patch_backend_support,
)
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
    """Validate the extracted script workspace and resolved script path."""
    issues: list[str] = []
    warnings: list[str] = []

    if not map_source.extracted_dir.exists():
        issues.append(f"Temporary script workspace does not exist: {map_source.extracted_dir}")
        return ValidationResult(is_valid=False, issues=issues, warnings=warnings)
    if not map_source.extracted_dir.is_dir():
        issues.append(f"Temporary script workspace is not a directory: {map_source.extracted_dir}")
        return ValidationResult(is_valid=False, issues=issues, warnings=warnings)

    script_relative = map_source.script_relative_path.as_posix()
    if not map_source.script_path.exists() or not map_source.script_path.is_file():
        issues.append(
            f"Script not found: detected path '{script_relative}' is missing from the temporary workspace."
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
    map_source: MapSourceContext | None = None,
    source_text: str | None = None,
    handler: MpqHandler | None = None,
    patched_script_path: Path | None = None,
    campaign_maps: list[CampaignMapEntry] | None = None,
    log_callback=None,
) -> ValidationResult:
    """Validate the current direct-replace injection job."""
    log = log_callback or (lambda _severity, _message: None)
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

    if input_type is InputType.CAMPAIGN_W3N:
        if campaign_maps is None:
            warnings.append("Load the campaign to scan embedded maps before patching.")
        elif not campaign_maps:
            issues.append("No embedded .w3x/.w3m maps were found in the selected campaign.")
        else:
            selected_entries = [entry for entry in campaign_maps if entry.selected]
            if not selected_entries:
                issues.append("Select at least one embedded campaign map before patching.")
            unreadable_entries = [entry for entry in selected_entries if not entry.patchable]
            for entry in unreadable_entries:
                warnings.append(
                    f"Embedded map is unreadable and cannot be patched yet: {entry.archive_path}. {entry.message}"
                )

    if map_source is not None:
        loaded_validation = validate_loaded_map_source(map_source)
        issues.extend(loaded_validation.issues)
        warnings.extend(loaded_validation.warnings)
        source_text = map_source.source_text

        capabilities = None
        try:
            if handler is None:
                handler, capabilities = resolve_patch_backend(
                    external_listfiles=map_source.external_listfiles,
                    log_callback=log,
                )
            else:
                capabilities = get_patch_capabilities(handler)
                log("INFO", f"Backend selected: {describe_backend(handler)}")
                log("INFO", f"Backend capabilities: {format_patch_capabilities(capabilities)}")
        except Exception as exc:
            issues.append(str(exc))
            handler = None
            capabilities = None

        if handler is not None and capabilities is not None:
            backend_validation = validate_patch_backend_support(handler, capabilities)
            log(
                "INFO",
                f"Validation patch capability result: {'pass' if backend_validation.is_valid else 'fail'}",
            )
            issues.extend(backend_validation.issues)
            warnings.extend(backend_validation.warnings)

        script_entry_path = map_source.script_relative_path.as_posix()
        if not script_entry_path:
            issues.append("Detected script path is required for direct replacement.")

        if handler is not None and script_entry_path:
            try:
                archive_entries = set(
                    handler.list_archive_entries_with_listfiles(
                        map_source.input_path,
                        external_listfiles=map_source.external_listfiles,
                    )
                )
                if script_entry_path not in archive_entries:
                    issues.append(
                        f"Detected script entry was not found inside the archive: {script_entry_path}"
                    )
            except Exception as exc:
                issues.append(str(exc))

    if patched_script_path is not None and not patched_script_path.is_file():
        issues.append(f"Patched script file was not generated successfully: {patched_script_path}")

    if source_text is not None:
        issues.extend(validate_map_source_text(source_text))
        warnings.extend(_effective_warnings(source_text, selected_patch))

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
