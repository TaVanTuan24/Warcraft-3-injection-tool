"""Optional CLI mode for the Warcraft 3 patcher."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from models import CampaignBuildSummary, PatchMode, PatchResult, PatchRunOptions, PatchSelection
from services.campaign_loader import dispose_campaign_source, list_campaign_maps, load_campaign_source
from services.injector import inject_and_build, inject_and_build_campaign, summarize_campaign_build
from services.input_detector import detect_input_type
from services.trigger_parser import parse_trigger_file
from utils import (
    ArchiveProcessingError,
    ConfigValidationError,
    JassPatchError,
    configure_logging,
)


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the optional CLI parser."""
    parser = argparse.ArgumentParser(
        description="Inject trigger content into readable Warcraft 3 maps or custom campaigns."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the input .w3x, .w3m, or .w3n archive.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the output .w3x, .w3m, or .w3n archive.",
    )
    parser.add_argument(
        "--trigger-file",
        dest="trigger_files",
        action="append",
        default=[],
        help="Path to an imported .j trigger file. Can be passed multiple times.",
    )
    parser.add_argument(
        "--map",
        dest="campaign_maps",
        action="append",
        default=[],
        help="Archive-relative campaign map path to patch. Can be passed multiple times.",
    )
    parser.add_argument(
        "--all-maps",
        action="store_true",
        help="Patch all readable embedded campaign maps.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting the output file.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary workspace after execution.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Abort campaign processing on the first failed embedded map.",
    )
    parser.add_argument(
        "--skip-failures",
        action="store_true",
        help="Continue campaign processing when an embedded map fails.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging output.",
    )
    parser.add_argument(
        "--patch-mode",
        choices=[mode.value for mode in PatchMode],
        default=PatchMode.AUTO.value,
        help="Map patch strategy: auto, fast_replace, or full_rebuild.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI mode."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)

    selection = PatchSelection.empty()
    for trigger_file in args.trigger_files:
        imported = parse_trigger_file(Path(trigger_file).expanduser().resolve())
        selection.extend_from_import(imported)

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    input_type = detect_input_type(input_path)
    stop_on_first_error = args.stop_on_error and not args.skip_failures
    options = PatchRunOptions(
        overwrite=args.overwrite,
        keep_temp=args.keep_temp,
        verbose=args.verbose,
        stop_on_first_error=stop_on_first_error,
        patch_mode=PatchMode(args.patch_mode),
    )

    if input_type.is_map:
        result = inject_and_build(
            input_map=input_path,
            output_map=output_path,
            selected_patch=selection,
            options=options,
            progress_callback=lambda step: LOGGER.info("Progress: %s", step),
            log_callback=lambda severity, message: LOGGER.log(
                _severity_to_level(severity), message
            ),
        )
        _log_single_result(result)
        return 0

    campaign_context = load_campaign_source(
        input_campaign=input_path,
        progress_callback=lambda step: LOGGER.info("Progress: %s", step),
        log_callback=lambda severity, message: LOGGER.log(
            _severity_to_level(severity), message
        ),
    )
    try:
        entries = list_campaign_maps(
            campaign_context,
            progress_callback=lambda step: LOGGER.info("Progress: %s", step),
            log_callback=lambda severity, message: LOGGER.log(
                _severity_to_level(severity), message
            ),
        )
        if args.campaign_maps:
            requested = {path.replace("\\", "/") for path in args.campaign_maps}
            available = {entry.archive_path for entry in entries}
            missing = sorted(requested - available)
            if missing:
                raise ValueError(
                    "Unknown campaign map path(s): " + ", ".join(missing)
                )
            for entry in entries:
                entry.selected = entry.archive_path in requested and entry.patchable
        elif args.all_maps or not args.campaign_maps:
            for entry in entries:
                entry.selected = entry.patchable

        summary = inject_and_build_campaign(
            campaign_context=campaign_context,
            output_campaign=output_path,
            selected_patch=selection,
            selected_maps=[entry for entry in entries if entry.selected],
            options=options,
            progress_callback=lambda step: LOGGER.info("Progress: %s", step),
            log_callback=lambda severity, message: LOGGER.log(
                _severity_to_level(severity), message
            ),
        )
        _log_campaign_result(summary)
        return 0
    finally:
        dispose_campaign_source(
            campaign_context,
            keep=args.keep_temp,
            log_callback=lambda severity, message: LOGGER.log(
                _severity_to_level(severity), message
            ),
        )


def _log_single_result(result: PatchResult) -> None:
    LOGGER.info(
        "Built patched archive: %s (%d globals, %d functions, %d calls added).",
        result.output_path,
        result.added_globals,
        result.added_functions,
        result.added_init_calls,
    )


def _log_campaign_result(summary: CampaignBuildSummary) -> None:
    LOGGER.info(
        "Built patched campaign: %s (selected=%d, succeeded=%d, skipped=%d, failed=%d).",
        summary.output_path,
        summary.selected_maps,
        summary.succeeded_maps,
        summary.skipped_maps,
        summary.failed_maps,
    )
    for line in summarize_campaign_build(summary).splitlines():
        LOGGER.info(line)


def _severity_to_level(severity: str) -> int:
    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "SUCCESS": logging.INFO,
    }
    return mapping.get(severity.upper(), logging.INFO)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        ArchiveProcessingError,
        ConfigValidationError,
        JassPatchError,
        FileNotFoundError,
        ValueError,
    ) as exc:
        LOGGER.error(str(exc))
        raise SystemExit(1) from exc
