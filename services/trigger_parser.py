"""Parser for imported JASS trigger files."""

from __future__ import annotations

import re
from pathlib import Path

from models import (
    FunctionEntry,
    GlobalEntry,
    MainCallEntry,
    TriggerImportResult,
    make_id,
)
from utils import ConfigValidationError, normalize_for_match


GLOBALS_BLOCK_PATTERN = re.compile(
    r"(?P<block>^[ \t]*globals\b.*?^[ \t]*endglobals\b(?:\r?\n)?)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
FUNCTION_BLOCK_PATTERN = re.compile(
    r"(?P<block>^[ \t]*function\b.*?^[ \t]*endfunction\b(?:\r?\n)?)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
FUNCTION_SIGNATURE_PATTERN = re.compile(
    r"^[ \t]*function[ \t]+(?P<name>[A-Za-z0-9_]+)[ \t]+takes\b(?P<rest>.*)$",
    re.IGNORECASE | re.MULTILINE,
)
MAIN_SIGNATURE_PATTERN = re.compile(
    r"^[ \t]*function[ \t]+main[ \t]+takes[ \t]+nothing[ \t]+returns[ \t]+nothing\b",
    re.IGNORECASE | re.MULTILINE,
)
CALL_LINE_PATTERN = re.compile(r"^[ \t]*call\b.+$", re.IGNORECASE | re.MULTILINE)


def parse_trigger_file(path: Path) -> TriggerImportResult:
    """Parse a JASS trigger file into importable entries."""
    if not path.is_file():
        raise FileNotFoundError(f"Trigger file not found: {path}")
    if path.suffix.lower() != ".j":
        raise ConfigValidationError(
            f"Unsupported trigger file type '{path.suffix}'. Only .j files are supported."
        )

    text = path.read_text(encoding="utf-8")
    source_name = str(path)
    globals_entries = _extract_globals(text, source_name)
    function_entries, main_calls = _extract_functions_and_calls(text, source_name)
    return TriggerImportResult(
        source_file=source_name,
        globals_entries=globals_entries,
        function_entries=function_entries,
        main_call_entries=main_calls,
    )


def parse_globals_text(text: str, source_file: str = "manual") -> list[GlobalEntry]:
    """Parse raw global declarations."""
    entries: list[GlobalEntry] = []
    for line in text.splitlines():
        candidate = line.strip()
        if candidate:
            entries.append(
                GlobalEntry(
                    id=make_id("global"),
                    enabled=True,
                    source_file=source_file,
                    text=candidate,
                    name=_guess_global_name(candidate),
                )
            )
    return entries


def parse_main_calls_text(text: str, source_file: str = "manual") -> list[MainCallEntry]:
    """Parse raw main-call lines."""
    entries: list[MainCallEntry] = []
    for line in text.splitlines():
        candidate = line.strip()
        if candidate:
            entries.append(
                MainCallEntry(
                    id=make_id("call"),
                    enabled=True,
                    source_file=source_file,
                    text=candidate,
                    name=_guess_call_name(candidate),
                )
            )
    return entries


def parse_functions_text(text: str, source_file: str = "manual") -> list[FunctionEntry]:
    """Parse raw function blocks."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [
        match.group("block").strip("\n")
        for match in FUNCTION_BLOCK_PATTERN.finditer(normalized)
    ]
    entries: list[FunctionEntry] = []
    for block in blocks:
        signature_match = FUNCTION_SIGNATURE_PATTERN.search(block)
        if not signature_match:
            raise ConfigValidationError("Raw function text contains an invalid function block.")
        function_name = signature_match.group("name")
        if function_name.lower() == "main":
            continue
        entries.append(
            FunctionEntry(
                id=make_id("function"),
                enabled=True,
                source_file=source_file,
                text=block,
                signature=normalize_for_match(signature_match.group(0)),
                name=function_name,
            )
        )
    return entries


def _extract_globals(text: str, source_file: str) -> list[GlobalEntry]:
    match = GLOBALS_BLOCK_PATTERN.search(text)
    if not match:
        return []
    block_lines = match.group("block").splitlines()
    entries: list[GlobalEntry] = []
    for line in block_lines[1:-1]:
        candidate = line.strip()
        if candidate and not candidate.startswith("//"):
            entries.append(
                GlobalEntry(
                    id=make_id("global"),
                    enabled=True,
                    source_file=source_file,
                    text=candidate,
                    name=_guess_global_name(candidate),
                )
            )
    return entries


def _extract_functions_and_calls(
    text: str, source_file: str
) -> tuple[list[FunctionEntry], list[MainCallEntry]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    function_entries: list[FunctionEntry] = []
    main_call_entries: list[MainCallEntry] = []

    for match in FUNCTION_BLOCK_PATTERN.finditer(normalized):
        block = match.group("block").strip("\n")
        signature_match = FUNCTION_SIGNATURE_PATTERN.search(block)
        if not signature_match:
            continue
        function_name = signature_match.group("name")
        signature = normalize_for_match(signature_match.group(0))
        if function_name.lower() == "main":
            for call_match in CALL_LINE_PATTERN.finditer(block):
                call_text = call_match.group(0).strip()
                main_call_entries.append(
                    MainCallEntry(
                        id=make_id("call"),
                        enabled=True,
                        source_file=source_file,
                        text=call_text,
                        name=_guess_call_name(call_text),
                    )
                )
            continue

        function_entries.append(
            FunctionEntry(
                id=make_id("function"),
                enabled=True,
                source_file=source_file,
                text=block,
                signature=signature,
                name=function_name,
            )
        )

    return function_entries, main_call_entries


def _guess_global_name(text: str) -> str:
    parts = text.split()
    if not parts:
        return ""
    if "=" in parts:
        eq_index = parts.index("=")
        if eq_index > 0:
            return parts[eq_index - 1]
    if len(parts) >= 2:
        return parts[1]
    return parts[0]


def _guess_call_name(text: str) -> str:
    candidate = text.strip()
    if "(" in candidate:
        candidate = candidate.split("(", 1)[0]
    return candidate.replace("call ", "", 1).strip()
