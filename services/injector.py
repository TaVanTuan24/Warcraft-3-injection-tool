"""Patch injection services."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

from jass_patcher import get_effective_patch_config, patch_war3map_j
from models import (
    CampaignBuildSummary,
    CampaignMapEntry,
    CampaignPatchResult,
    MapSourceContext,
    MpqBackendType,
    PatchMode,
    PatchConfig,
    PatchResult,
    PatchRunOptions,
    PatchSelection,
    PatchedSourceResult,
)
from mpq_handler import MpqHandler
from services.builder import build_map
from services.campaign_loader import (
    build_campaign_archive,
    extract_campaign_map,
    replace_campaign_map,
)
from services.input_detector import detect_input_type
from services.map_loader import dispose_map_source, load_map_source
from services.validator import validate_before_inject, validate_fast_replace_preconditions
from utils import ArchiveProcessingError, cleanup_workspace, create_temp_workspace, ensure_output_target_is_safe


def selection_to_patch_config(selection: PatchSelection) -> PatchConfig:
    """Convert enabled entries into a text patch config."""
    return PatchConfig(
        globals_to_add=[entry.text for entry in selection.enabled_globals()],
        functions_to_add=[entry.text for entry in selection.enabled_functions()],
        init_calls=[entry.text for entry in selection.enabled_main_calls()],
    )


def effective_selection_for_map(
    source_text: str,
    selection: PatchSelection,
) -> PatchSelection:
    """Filter enabled entries down to only content not already present."""
    effective_config = get_effective_patch_config(source_text, selection_to_patch_config(selection))

    globals_entries = _filter_globals(selection.enabled_globals(), effective_config.globals_to_add)
    function_entries = _filter_functions(
        selection.enabled_functions(), effective_config.functions_to_add
    )
    main_call_entries = _filter_calls(
        selection.enabled_main_calls(), effective_config.init_calls
    )
    return PatchSelection(
        globals_entries=globals_entries,
        function_entries=function_entries,
        main_call_entries=main_call_entries,
    )


def inject_patch(
    map_source: MapSourceContext,
    selected_patch: PatchSelection,
) -> PatchedSourceResult:
    """Inject enabled patch content into an extracted map source."""
    effective_selection = effective_selection_for_map(map_source.source_text, selected_patch)
    patch_result = patch_war3map_j(
        map_source.script_path,
        selection_to_patch_config(selected_patch),
    )
    patched_text = map_source.script_path.read_text(encoding="utf-8")
    map_source.source_text = patched_text
    return PatchedSourceResult(
        effective_selection=effective_selection,
        patch_result=patch_result,
        patched_text=patched_text,
    )


def inject_and_build(
    input_map: Path,
    output_map: Path,
    selected_patch: PatchSelection,
    options: PatchRunOptions,
    external_listfiles: Sequence[Path] | None = None,
    progress_callback=None,
    log_callback=None,
) -> PatchResult:
    """Full safe workflow for one readable map: load, validate, inject, rebuild, cleanup."""
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)

    input_type = detect_input_type(input_map)
    if not input_type.is_map:
        raise ArchiveProcessingError("Single-map injection only supports .w3x and .w3m inputs.")

    ensure_output_target_is_safe(
        input_path=input_map,
        output_path=output_map,
        overwrite=options.overwrite,
    )
    validation = validate_before_inject(
        input_map=input_map,
        output_map=output_map,
        selected_patch=selected_patch,
        overwrite=options.overwrite,
        patch_mode=options.patch_mode,
    )
    if not validation.is_valid:
        raise ValueError("\n".join(validation.issues))

    map_source = load_map_source(
        input_war3_archive=input_map,
        external_listfiles=external_listfiles,
        progress_callback=progress,
        log_callback=log,
    )
    try:
        progress("validating target map")
        log("INFO", f"Using detected script: {map_source.script_relative_path.as_posix()}")
        validation = validate_before_inject(
            input_map=input_map,
            output_map=output_map,
            selected_patch=selected_patch,
            overwrite=options.overwrite,
            patch_mode=options.patch_mode,
            map_source=map_source,
        )
        if not validation.is_valid:
            raise ValueError("\n".join(validation.issues))
        for warning in validation.warnings:
            log("WARNING", warning)
        patched_source = inject_patch(map_source, selected_patch)
        log("INFO", f"Patch mode selected: {options.patch_mode.label}")
        return _write_patched_map_archive(
            input_map=input_map,
            output_map=output_map,
            map_source=map_source,
            patched_source=patched_source,
            options=options,
            progress_callback=progress,
            log_callback=log,
        )
    finally:
        dispose_map_source(map_source, keep=options.keep_temp, log_callback=log)


def inject_and_build_campaign(
    campaign_context,
    output_campaign: Path,
    selected_patch: PatchSelection,
    selected_maps: list[CampaignMapEntry],
    options: PatchRunOptions,
    progress_callback=None,
    log_callback=None,
) -> CampaignBuildSummary:
    """Patch selected readable campaign maps and rebuild a new .w3n archive."""
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)

    if options.patch_mode is PatchMode.FAST_REPLACE:
        log("WARNING", "Campaign patching does not support fast replace. Using full rebuild.")

    ensure_output_target_is_safe(
        input_path=campaign_context.input_path,
        output_path=output_campaign,
        overwrite=options.overwrite,
    )
    validation = validate_before_inject(
        input_map=campaign_context.input_path,
        output_map=output_campaign,
        selected_patch=selected_patch,
        overwrite=options.overwrite,
        patch_mode=PatchMode.FULL_REBUILD,
        campaign_maps=campaign_context.map_entries,
        stop_on_first_error=options.stop_on_first_error,
    )
    if not validation.is_valid:
        raise ValueError("\n".join(validation.issues))

    selected_by_id = {entry.id: entry for entry in selected_maps}
    per_map_results: list[CampaignPatchResult] = []
    processed_successfully = 0
    total_maps = len(campaign_context.map_entries)
    total_selected = len(selected_maps)

    for map_index, map_entry in enumerate(campaign_context.map_entries, start=1):
        current_entry = selected_by_id.get(map_entry.id, map_entry)
        if not current_entry.selected:
            per_map_results.append(
                CampaignPatchResult(
                    map_entry_id=current_entry.id,
                    archive_path=current_entry.archive_path,
                    map_name=current_entry.map_name,
                    selected=False,
                    succeeded=False,
                    skipped=True,
                    failed=False,
                    message="Not selected.",
                )
            )
            continue

        progress(f"processing map {map_index}/{total_maps}")
        log(
            "INFO",
            f"Processing campaign map {map_index}/{total_maps}: {current_entry.archive_path}",
        )

        if not current_entry.patchable:
            result = CampaignPatchResult(
                map_entry_id=current_entry.id,
                archive_path=current_entry.archive_path,
                map_name=current_entry.map_name,
                selected=True,
                succeeded=False,
                skipped=not options.stop_on_first_error,
                failed=True,
                message=current_entry.message or "Map is unreadable or unpatchable.",
            )
            per_map_results.append(result)
            if options.stop_on_first_error:
                raise ArchiveProcessingError(result.message)
            log("ERROR", f"Skipping failed campaign map: {current_entry.archive_path}. {result.message}")
            continue

        map_source = extract_campaign_map(
            campaign_context,
            current_entry,
            progress_callback=progress,
            log_callback=log,
        )
        try:
            patched_source = inject_patch(map_source, selected_patch)
            temp_output_root = create_temp_workspace(
                "war3campaign_map_build_",
                logger=_CallbackLogger(log),
            )
            temp_output = temp_output_root / current_entry.map_name
            try:
                log("INFO", f"Rebuilding embedded map: {current_entry.archive_path}")
                build_map(map_source.extracted_dir, temp_output, log_callback=log)
                log("INFO", f"Replacing embedded map in campaign: {current_entry.archive_path}")
                replace_campaign_map(campaign_context, current_entry, temp_output)
            finally:
                cleanup_workspace(
                    temp_output_root,
                    keep=options.keep_temp,
                    logger=_CallbackLogger(log),
                )

            duplicate_counts = _calculate_duplicate_counts(selected_patch, patched_source.effective_selection)
            message = (
                f"Patched successfully: globals={patched_source.patch_result.added_globals}, "
                f"functions={patched_source.patch_result.added_functions}, "
                f"main calls={patched_source.patch_result.added_init_calls}."
            )
            per_map_results.append(
                CampaignPatchResult(
                    map_entry_id=current_entry.id,
                    archive_path=current_entry.archive_path,
                    map_name=current_entry.map_name,
                    selected=True,
                    succeeded=True,
                    skipped=False,
                    failed=False,
                    added_globals=patched_source.patch_result.added_globals,
                    added_functions=patched_source.patch_result.added_functions,
                    added_init_calls=patched_source.patch_result.added_init_calls,
                    duplicate_globals_skipped=duplicate_counts[0],
                    duplicate_functions_skipped=duplicate_counts[1],
                    duplicate_main_calls_skipped=duplicate_counts[2],
                    message=message,
                )
            )
            processed_successfully += 1
        except Exception as exc:
            result = CampaignPatchResult(
                map_entry_id=current_entry.id,
                archive_path=current_entry.archive_path,
                map_name=current_entry.map_name,
                selected=True,
                succeeded=False,
                skipped=not options.stop_on_first_error,
                failed=True,
                message=str(exc),
            )
            per_map_results.append(result)
            if options.stop_on_first_error:
                raise
            log("ERROR", f"Failed campaign map {current_entry.archive_path}: {exc}")
        finally:
            dispose_map_source(map_source, keep=False, log_callback=log)

    if processed_successfully == 0:
        raise ArchiveProcessingError(
            "No selected campaign maps could be processed successfully. No output campaign was built."
        )

    progress("rebuilding campaign")
    log("INFO", f"Rebuilding campaign archive: {output_campaign}")
    build_campaign_archive(campaign_context, output_campaign, log_callback=log)
    progress("built")

    summary = CampaignBuildSummary(
        output_path=output_campaign,
        total_maps_found=total_maps,
        selected_maps=total_selected,
        succeeded_maps=sum(1 for result in per_map_results if result.succeeded),
        skipped_maps=sum(1 for result in per_map_results if result.skipped),
        failed_maps=sum(1 for result in per_map_results if result.failed),
        per_map_results=per_map_results,
    )
    log("SUCCESS", f"Built patched campaign: {output_campaign}")
    return summary


def summarize_campaign_build(summary: CampaignBuildSummary) -> str:
    """Create a readable text summary for campaign patch/build results."""
    lines = [
        f"Patched campaign created: {summary.output_path}",
        f"Total maps found: {summary.total_maps_found}",
        f"Selected maps: {summary.selected_maps}",
        f"Successfully patched maps: {summary.succeeded_maps}",
        f"Skipped maps: {summary.skipped_maps}",
        f"Failed maps: {summary.failed_maps}",
        "",
        "Per-map results:",
    ]
    for result in summary.per_map_results:
        lines.append(
            (
                f"- {result.archive_path}: "
                f"{'success' if result.succeeded else 'failed' if result.failed else 'skipped'} | "
                f"globals={result.added_globals}, functions={result.added_functions}, "
                f"main calls={result.added_init_calls}, dupes skipped="
                f"{result.duplicate_globals_skipped + result.duplicate_functions_skipped + result.duplicate_main_calls_skipped} | "
                f"{result.message}"
            )
        )
    return "\n".join(lines)


def _write_patched_map_archive(
    input_map: Path,
    output_map: Path,
    map_source: MapSourceContext,
    patched_source: PatchedSourceResult,
    options: PatchRunOptions,
    progress_callback=None,
    log_callback=None,
) -> PatchResult:
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)

    if options.patch_mode is PatchMode.FULL_REBUILD:
        return _build_patched_map_archive(
            output_map=output_map,
            map_source=map_source,
            patched_source=patched_source,
            progress_callback=progress,
            log_callback=log,
        )

    if options.patch_mode is PatchMode.FAST_REPLACE:
        return _fast_replace_patched_map_archive(
            input_map=input_map,
            output_map=output_map,
            map_source=map_source,
            patched_source=patched_source,
            progress_callback=progress,
            log_callback=log,
        )

    try:
        return _fast_replace_patched_map_archive(
            input_map=input_map,
            output_map=output_map,
            map_source=map_source,
            patched_source=patched_source,
            progress_callback=progress,
            log_callback=log,
        )
    except Exception as exc:
        log("WARNING", f"Fast replace failed, falling back to full rebuild. {exc}")
        return _build_patched_map_archive(
            output_map=output_map,
            map_source=map_source,
            patched_source=patched_source,
            progress_callback=progress,
            log_callback=log,
        )


def _build_patched_map_archive(
    output_map: Path,
    map_source: MapSourceContext,
    patched_source: PatchedSourceResult,
    progress_callback=None,
    log_callback=None,
) -> PatchResult:
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)

    progress("injecting globals")
    progress("injecting functions")
    progress("injecting main calls")
    progress("rebuilding archive")
    log("INFO", "Using full rebuild.")
    build_map(map_source.extracted_dir, output_map, log_callback=log)
    progress("built")
    log("SUCCESS", f"Built patched archive: {output_map}")
    return PatchResult(
        added_globals=patched_source.patch_result.added_globals,
        added_functions=patched_source.patch_result.added_functions,
        added_init_calls=patched_source.patch_result.added_init_calls,
        output_path=output_map,
    )


def _fast_replace_patched_map_archive(
    input_map: Path,
    output_map: Path,
    map_source: MapSourceContext,
    patched_source: PatchedSourceResult,
    progress_callback=None,
    log_callback=None,
) -> PatchResult:
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)

    progress("validating fast replace")
    log("INFO", "Using fast replace.")
    handler = MpqHandler.auto_detect(preferred_backend_type=MpqBackendType.MPQEDITOR)
    validation = validate_fast_replace_preconditions(
        input_map=input_map,
        map_source=map_source,
        patched_script_path=map_source.script_path,
        handler=handler,
    )
    if not validation.is_valid:
        raise ArchiveProcessingError(" ".join(validation.issues))
    for warning in validation.warnings:
        log("WARNING", warning)

    progress("copying output archive")
    log("INFO", f"Copying input archive to output archive: {input_map} -> {output_map}")
    target_archive, copied_to_temp = _prepare_fast_replace_output(
        input_map=input_map,
        output_map=output_map,
        workspace_root=map_source.workspace_root,
    )
    succeeded = False
    try:
        script_entry_path = map_source.script_relative_path.as_posix()
        progress("replacing script in archive")
        log("INFO", f"Locating script entry: {script_entry_path}")
        log("INFO", f"Replacing script entry in archive: {script_entry_path}")
        handler.replace_file_in_archive(
            archive_path=target_archive,
            archive_entry_path=script_entry_path,
            local_file_path=map_source.script_path,
        )
        if copied_to_temp:
            shutil.copy2(target_archive, output_map)
        succeeded = True
        progress("built")
        log("SUCCESS", f"Fast replace succeeded: {output_map}")
        return PatchResult(
            added_globals=patched_source.patch_result.added_globals,
            added_functions=patched_source.patch_result.added_functions,
            added_init_calls=patched_source.patch_result.added_init_calls,
            output_path=output_map,
        )
    finally:
        if copied_to_temp:
            target_archive.unlink(missing_ok=True)
        elif not succeeded and target_archive.exists():
            target_archive.unlink(missing_ok=True)


def _prepare_fast_replace_output(
    input_map: Path,
    output_map: Path,
    workspace_root: Path,
) -> tuple[Path, bool]:
    output_map.parent.mkdir(parents=True, exist_ok=True)
    if input_map.resolve() != output_map.resolve():
        shutil.copy2(input_map, output_map)
        return output_map, False

    temp_output = workspace_root / f"__fast_replace_copy{output_map.suffix}"
    shutil.copy2(input_map, temp_output)
    return temp_output, True


class _CallbackLogger:
    def __init__(self, callback) -> None:
        self._callback = callback

    def info(self, message: str, *args: object) -> None:
        self._callback("INFO", message % args if args else message)

    def debug(self, message: str, *args: object) -> None:
        self._callback("INFO", message % args if args else message)

    def warning(self, message: str, *args: object) -> None:
        self._callback("WARNING", message % args if args else message)


def _calculate_duplicate_counts(
    selected_patch: PatchSelection,
    effective_selection: PatchSelection,
) -> tuple[int, int, int]:
    return (
        len(selected_patch.enabled_globals()) - len(effective_selection.enabled_globals()),
        len(selected_patch.enabled_functions()) - len(effective_selection.enabled_functions()),
        len(selected_patch.enabled_main_calls()) - len(effective_selection.enabled_main_calls()),
    )


def _filter_globals(entries, kept_values: list[str]) -> list:
    remaining = [value.strip() for value in kept_values]
    result = []
    for entry in entries:
        candidate = entry.text.strip()
        if candidate in remaining:
            remaining.remove(candidate)
            result.append(entry)
    return result


def _filter_functions(entries, kept_values: list[str]) -> list:
    remaining = list(kept_values)
    result = []
    for entry in entries:
        if entry.text in remaining:
            remaining.remove(entry.text)
            result.append(entry)
    return result


def _filter_calls(entries, kept_values: list[str]) -> list:
    remaining = [value.strip() for value in kept_values]
    result = []
    for entry in entries:
        candidate = entry.text.strip()
        if candidate in remaining:
            remaining.remove(candidate)
            result.append(entry)
    return result
