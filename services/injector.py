"""Direct archive patch injection services."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

from jass_patcher import get_effective_patch_config, patch_war3map_j
from models import (
    CampaignBuildSummary,
    CampaignContext,
    CampaignMapEntry,
    CampaignPatchResult,
    MapSourceContext,
    PatchConfig,
    PatchResult,
    PatchRunOptions,
    PatchSelection,
    PatchedSourceResult,
)
from services.input_detector import detect_input_type
from services.map_loader import dispose_map_source, load_map_source
from services.patch_support import (
    resolve_patch_backend,
    validate_patch_backend_support,
)
from services.validator import validate_before_inject
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
    """Inject enabled patch content into the temporary extracted script."""
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
    """Load a map script, patch it, copy the input map, and replace the script entry."""
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)

    input_type = detect_input_type(input_map)
    if not input_type.is_map:
        raise ArchiveProcessingError("Direct injection only supports .w3x and .w3m inputs.")

    ensure_output_target_is_safe(
        input_path=input_map,
        output_path=output_map,
        overwrite=options.overwrite,
    )
    initial_validation = validate_before_inject(
        input_map=input_map,
        output_map=output_map,
        selected_patch=selected_patch,
        overwrite=options.overwrite,
        log_callback=log,
    )
    if not initial_validation.is_valid:
        raise ValueError("\n".join(initial_validation.issues))

    handler, capabilities = resolve_patch_backend(
        external_listfiles=external_listfiles,
        log_callback=log,
    )
    backend_validation = validate_patch_backend_support(handler, capabilities)
    log(
        "INFO",
        f"Inject patch capability result: {'pass' if backend_validation.is_valid else 'fail'}",
    )
    if not backend_validation.is_valid:
        raise ValueError("\n".join(backend_validation.issues))
    map_source = load_map_source(
        input_war3_archive=input_map,
        external_listfiles=external_listfiles,
        progress_callback=progress,
        log_callback=log,
    )
    try:
        progress("validating target map")
        validation = validate_before_inject(
            input_map=input_map,
            output_map=output_map,
            selected_patch=selected_patch,
            overwrite=options.overwrite,
            map_source=map_source,
            source_text=map_source.source_text,
            handler=handler,
            log_callback=log,
        )
        if not validation.is_valid:
            raise ValueError("\n".join(validation.issues))
        for warning in validation.warnings:
            log("WARNING", warning)

        progress("patching script content")
        log("INFO", "Patching script content.")
        patched_source = inject_patch(map_source, selected_patch)

        validation = validate_before_inject(
            input_map=input_map,
            output_map=output_map,
            selected_patch=selected_patch,
            overwrite=options.overwrite,
            map_source=map_source,
            source_text=patched_source.patched_text,
            handler=handler,
            patched_script_path=map_source.script_path,
            log_callback=log,
        )
        if not validation.is_valid:
            raise ValueError("\n".join(validation.issues))

        return _replace_script_entry(
            input_map=input_map,
            output_map=output_map,
            map_source=map_source,
            patched_source=patched_source,
            handler=handler,
            progress_callback=progress,
            log_callback=log,
        )
    finally:
        dispose_map_source(map_source, keep=options.keep_temp, log_callback=log)


def inject_and_build_campaign(
    campaign_context: CampaignContext,
    output_campaign: Path,
    selected_patch: PatchSelection,
    selected_maps: list[CampaignMapEntry],
    options: PatchRunOptions,
    progress_callback=None,
    log_callback=None,
) -> CampaignBuildSummary:
    """Patch selected embedded maps and replace them back into a copied campaign archive."""
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)

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
        campaign_maps=selected_maps,
        log_callback=log,
    )
    if not validation.is_valid:
        raise ValueError("\n".join(validation.issues))
    for warning in validation.warnings:
        log("WARNING", warning)

    handler, capabilities = resolve_patch_backend(
        external_listfiles=campaign_context.external_listfiles,
        log_callback=log,
    )
    backend_validation = validate_patch_backend_support(handler, capabilities)
    log(
        "INFO",
        f"Inject patch capability result: {'pass' if backend_validation.is_valid else 'fail'}",
    )
    if not backend_validation.is_valid:
        raise ValueError("\n".join(backend_validation.issues))

    output_campaign.parent.mkdir(parents=True, exist_ok=True)
    log("INFO", f"Copying input campaign to output campaign: {campaign_context.input_path} -> {output_campaign}")
    shutil.copy2(campaign_context.input_path, output_campaign)

    selected_by_id = {entry.id: entry for entry in selected_maps}
    selected_entries = [entry for entry in selected_maps if entry.selected]
    total_selected = len(selected_entries)
    total_maps = len(campaign_context.map_entries)
    processed_selected = 0
    succeeded_maps = 0
    skipped_maps = 0
    failed_maps = 0
    per_map_results: list[CampaignPatchResult] = []

    for original_entry in campaign_context.map_entries:
        current_entry = selected_by_id.get(original_entry.id, original_entry)
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

        processed_selected += 1
        progress(f"processing map {processed_selected}/{total_selected}")
        log(
            "INFO",
            f"Processing campaign map {processed_selected}/{total_selected}: {current_entry.archive_path}",
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
            failed_maps += 1
            log("ERROR", f"Campaign map failed: {current_entry.archive_path}. {result.message}")
            if options.stop_on_first_error:
                raise ArchiveProcessingError(result.message)
            skipped_maps += 1
            continue

        embedded_map_path = campaign_context.extracted_dir / Path(current_entry.archive_path)
        if not embedded_map_path.is_file():
            message = f"Embedded map file not found in extracted campaign: {embedded_map_path}"
            result = CampaignPatchResult(
                map_entry_id=current_entry.id,
                archive_path=current_entry.archive_path,
                map_name=current_entry.map_name,
                selected=True,
                succeeded=False,
                skipped=not options.stop_on_first_error,
                failed=True,
                message=message,
            )
            per_map_results.append(result)
            failed_maps += 1
            log("ERROR", f"Campaign map failed: {current_entry.archive_path}. {message}")
            if options.stop_on_first_error:
                raise ArchiveProcessingError(message)
            skipped_maps += 1
            continue

        temp_output_root = create_temp_workspace(
            "war3campaign_map_build_",
            logger=_CallbackLogger(log),
        )
        temp_output_map = temp_output_root / current_entry.map_name
        try:
            patch_result = inject_and_build(
                input_map=embedded_map_path,
                output_map=temp_output_map,
                selected_patch=selected_patch,
                options=PatchRunOptions(
                    overwrite=True,
                    keep_temp=options.keep_temp,
                    verbose=options.verbose,
                    stop_on_first_error=options.stop_on_first_error,
                ),
                external_listfiles=campaign_context.external_listfiles,
                progress_callback=lambda step, prefix=current_entry.archive_path: progress(
                    f"{step} [{prefix}]"
                ),
                log_callback=log,
            )
            log("INFO", f"Replacing embedded map in output campaign: {current_entry.archive_path}")
            handler.replace_file_in_archive(
                archive_path=output_campaign,
                archive_entry_path=current_entry.archive_path,
                local_file_path=temp_output_map,
            )
            message = (
                f"Patched successfully (globals={patch_result.added_globals}, "
                f"functions={patch_result.added_functions}, main calls={patch_result.added_init_calls})."
            )
            log("SUCCESS", f"Campaign map patched successfully: {current_entry.archive_path}. {message}")
            per_map_results.append(
                CampaignPatchResult(
                    map_entry_id=current_entry.id,
                    archive_path=current_entry.archive_path,
                    map_name=current_entry.map_name,
                    selected=True,
                    succeeded=True,
                    skipped=False,
                    failed=False,
                    added_globals=patch_result.added_globals,
                    added_functions=patch_result.added_functions,
                    added_init_calls=patch_result.added_init_calls,
                    message=message,
                )
            )
            succeeded_maps += 1
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
            failed_maps += 1
            log("ERROR", f"Campaign map failed: {current_entry.archive_path}. {exc}")
            if options.stop_on_first_error:
                raise
            skipped_maps += 1
        finally:
            cleanup_workspace(
                temp_output_root,
                keep=options.keep_temp,
                logger=_CallbackLogger(log),
            )

    if succeeded_maps == 0:
        raise ArchiveProcessingError(
            "No selected campaign maps were patched successfully. Output campaign was not produced."
        )

    progress("inject completed")
    log("SUCCESS", f"Campaign inject completed successfully: {output_campaign}")
    return CampaignBuildSummary(
        output_path=output_campaign,
        total_maps_found=total_maps,
        selected_maps=total_selected,
        succeeded_maps=succeeded_maps,
        skipped_maps=skipped_maps,
        failed_maps=failed_maps,
        per_map_results=per_map_results,
    )


def summarize_campaign_build(summary: CampaignBuildSummary) -> str:
    """Create a readable summary for campaign patch results."""
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
        state = "success" if result.succeeded else "failed" if result.failed else "skipped"
        lines.append(
            (
                f"- {result.archive_path}: {state} | globals={result.added_globals}, "
                f"functions={result.added_functions}, main calls={result.added_init_calls} | "
                f"{result.message}"
            )
        )
    return "\n".join(lines)


def _replace_script_entry(
    input_map: Path,
    output_map: Path,
    map_source: MapSourceContext,
    patched_source: PatchedSourceResult,
    handler: MpqHandler,
    progress_callback=None,
    log_callback=None,
) -> PatchResult:
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)

    progress("copying input map to output map")
    output_map.parent.mkdir(parents=True, exist_ok=True)
    log("INFO", f"Copying input map to output map: {input_map} -> {output_map}")
    shutil.copy2(input_map, output_map)

    script_entry_path = map_source.script_relative_path.as_posix()
    progress("replacing script entry in archive")
    log("INFO", f"Replacing script entry in archive: {script_entry_path}")
    handler.replace_file_in_archive(
        archive_path=output_map,
        archive_entry_path=script_entry_path,
        local_file_path=map_source.script_path,
        external_listfiles=map_source.external_listfiles,
    )

    progress("inject completed")
    log("SUCCESS", "Inject completed successfully.")
    return PatchResult(
        added_globals=patched_source.patch_result.added_globals,
        added_functions=patched_source.patch_result.added_functions,
        added_init_calls=patched_source.patch_result.added_init_calls,
        output_path=output_map,
    )


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


class _CallbackLogger:
    def __init__(self, callback) -> None:
        self._callback = callback

    def info(self, message: str, *args: object) -> None:
        self._callback("INFO", message % args if args else message)

    def debug(self, message: str, *args: object) -> None:
        self._callback("INFO", message % args if args else message)

    def warning(self, message: str, *args: object) -> None:
        self._callback("WARNING", message % args if args else message)


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
