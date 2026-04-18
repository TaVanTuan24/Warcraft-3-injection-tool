"""Optional CLI mode for the direct-replace Warcraft 3 patcher."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from models import PatchResult, PatchRunOptions, PatchSelection
from services.injector import inject_and_build
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
        description="Inject trigger content into readable Warcraft 3 maps using direct script replacement."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the input .w3x or .w3m archive.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the output .w3x or .w3m archive.",
    )
    parser.add_argument(
        "--trigger-file",
        dest="trigger_files",
        action="append",
        default=[],
        help="Path to an imported .j trigger file. Can be passed multiple times.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting the output file.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary script workspace after execution.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging output.",
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
    if not input_type.is_map:
        raise ArchiveProcessingError("Direct CLI injection only supports .w3x and .w3m inputs.")

    options = PatchRunOptions(
        overwrite=args.overwrite,
        keep_temp=args.keep_temp,
        verbose=args.verbose,
    )

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


def _log_single_result(result: PatchResult) -> None:
    LOGGER.info(
        "Inject completed successfully: %s (%d globals, %d functions, %d calls added).",
        result.output_path,
        result.added_globals,
        result.added_functions,
        result.added_init_calls,
    )


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
