"""Idempotent patching logic for war3map.j."""

from __future__ import annotations

import re
from pathlib import Path

from models import PatchConfig, PatchResult
from utils import JassPatchError, detect_newline, normalize_for_match


GLOBALS_BLOCK_PATTERN = re.compile(
    r"(?P<block>^[ \t]*globals\b.*?^[ \t]*endglobals\b)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
MAIN_SIGNATURE_PATTERN = re.compile(
    r"^[ \t]*function[ \t]+main[ \t]+takes[ \t]+nothing[ \t]+returns[ \t]+nothing\b",
    re.IGNORECASE | re.MULTILINE,
)
END_FUNCTION_PATTERN_TEMPLATE = r"^[ \t]*endfunction\b"
FUNCTION_SIGNATURE_PATTERN = re.compile(
    r"^[ \t]*function[ \t]+(?P<name>[A-Za-z0-9_]+)[ \t]+takes\b",
    re.IGNORECASE | re.MULTILINE,
)


def patch_war3map_j(file_path: Path, patch_config: PatchConfig) -> PatchResult:
    """Patch the target war3map.j file in place."""
    original_text = file_path.read_text(encoding="utf-8")
    newline = detect_newline(original_text)

    updated_text, added_globals = _insert_globals(original_text, patch_config, newline)
    updated_text, added_functions = _insert_functions(
        updated_text, patch_config, newline
    )
    updated_text, added_init_calls = _insert_init_calls(
        updated_text, patch_config, newline
    )

    if updated_text != original_text:
        file_path.write_text(updated_text, encoding="utf-8", newline="")

    return PatchResult(
        added_globals=added_globals,
        added_functions=added_functions,
        added_init_calls=added_init_calls,
        output_path=file_path,
    )


def get_effective_patch_config(source_text: str, patch_config: PatchConfig) -> PatchConfig:
    """Compute the effective insertions that would be made against a source JASS file."""
    _require_globals_block(source_text)
    existing_globals = _existing_globals(source_text)
    existing_signatures = _existing_function_signatures(source_text)
    existing_calls = _existing_main_calls(source_text)

    globals_to_add = [
        entry for entry in patch_config.globals_to_add if entry.strip() not in existing_globals
    ]
    functions_to_add = []
    for function_text in patch_config.functions_to_add:
        signature = extract_function_signature(function_text)
        if signature not in existing_signatures:
            functions_to_add.append(function_text)
            existing_signatures.add(signature)
    init_calls = [
        call for call in patch_config.init_calls if call.strip() not in existing_calls
    ]

    return PatchConfig(
        globals_to_add=globals_to_add,
        functions_to_add=functions_to_add,
        init_calls=init_calls,
    )


def _insert_globals(
    source_text: str, patch_config: PatchConfig, newline: str
) -> tuple[str, int]:
    match = GLOBALS_BLOCK_PATTERN.search(source_text)
    if not match:
        raise JassPatchError("Missing required 'globals ... endglobals' block.")

    block_text = match.group("block")
    block_lines = block_text.splitlines()
    if not block_lines:
        raise JassPatchError("Globals block is malformed.")

    existing_entries = {line.strip() for line in block_lines[1:-1] if line.strip()}
    globals_to_add = [
        entry for entry in patch_config.globals_to_add if entry.strip() not in existing_entries
    ]
    if not globals_to_add:
        return source_text, 0

    indent = _detect_block_indent(block_lines[1:-1]) or "    "
    insertion_lines = [f"{indent}{entry.strip()}" for entry in globals_to_add]

    block_ends_with_newline = block_text.endswith(("\r\n", "\n", "\r"))
    rebuilt_lines = list(block_lines[:-1]) + insertion_lines + [block_lines[-1]]
    replacement = newline.join(rebuilt_lines)
    if block_ends_with_newline:
        replacement += newline

    start, end = match.span("block")
    updated_text = source_text[:start] + replacement + source_text[end:]
    return updated_text, len(globals_to_add)


def _insert_functions(
    source_text: str, patch_config: PatchConfig, newline: str
) -> tuple[str, int]:
    main_match = MAIN_SIGNATURE_PATTERN.search(source_text)
    if not main_match:
        raise JassPatchError(
            "Missing required 'function main takes nothing returns nothing' declaration."
        )

    functions_to_add = []
    existing_signatures = {
        normalize_for_match(signature.group(0))
        for signature in FUNCTION_SIGNATURE_PATTERN.finditer(source_text)
    }

    for function_text in patch_config.functions_to_add:
        signature = extract_function_signature(function_text)
        if signature not in existing_signatures:
            functions_to_add.append(_normalize_block(function_text, newline))
            existing_signatures.add(signature)

    if not functions_to_add:
        return source_text, 0

    insertion_text = ""
    for block in functions_to_add:
        insertion_text += block
        if not block.endswith(newline):
            insertion_text += newline
        insertion_text += newline

    insertion_point = main_match.start()
    updated_text = source_text[:insertion_point] + insertion_text + source_text[insertion_point:]
    return updated_text, len(functions_to_add)


def _insert_init_calls(
    source_text: str, patch_config: PatchConfig, newline: str
) -> tuple[str, int]:
    main_match = MAIN_SIGNATURE_PATTERN.search(source_text)
    if not main_match:
        raise JassPatchError(
            "Missing required 'function main takes nothing returns nothing' declaration."
        )

    main_start = main_match.start()
    end_function_pattern = re.compile(
        END_FUNCTION_PATTERN_TEMPLATE, re.IGNORECASE | re.MULTILINE
    )
    end_match = end_function_pattern.search(source_text, pos=main_match.end())
    if not end_match:
        raise JassPatchError("Main function is malformed: missing terminating 'endfunction'.")

    main_block = source_text[main_start:end_match.end()]
    body_lines = main_block.splitlines()
    if len(body_lines) < 2:
        raise JassPatchError("Main function body is malformed.")

    existing_lines = {line.strip() for line in body_lines[1:-1] if line.strip()}
    calls_to_add = [
        call for call in patch_config.init_calls if call.strip() not in existing_lines
    ]
    if not calls_to_add:
        return source_text, 0

    indent = _detect_block_indent(body_lines[1:-1]) or "    "
    insertion_lines = [f"{indent}{call.strip()}" for call in calls_to_add]
    rebuilt_lines = list(body_lines[:-1]) + insertion_lines + [body_lines[-1]]

    main_had_trailing_newline = main_block.endswith(("\r\n", "\n", "\r"))
    replacement = newline.join(rebuilt_lines)
    if main_had_trailing_newline:
        replacement += newline

    updated_text = source_text[:main_start] + replacement + source_text[end_match.end():]
    return updated_text, len(calls_to_add)


def extract_function_signature(function_text: str) -> str:
    match = FUNCTION_SIGNATURE_PATTERN.search(function_text)
    if not match:
        raise JassPatchError(
            "Malformed patch config: each function block must contain a valid JASS function signature."
        )
    return normalize_for_match(match.group(0))


def _normalize_block(block_text: str, newline: str) -> str:
    normalized = block_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return normalized.replace("\n", newline)


def _detect_block_indent(lines: list[str]) -> str:
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("//"):
            return line[: len(line) - len(line.lstrip())]
    return ""


def _require_globals_block(source_text: str) -> re.Match[str]:
    match = GLOBALS_BLOCK_PATTERN.search(source_text)
    if not match:
        raise JassPatchError("Missing required 'globals ... endglobals' block.")
    return match


def _require_main_match(source_text: str) -> re.Match[str]:
    match = MAIN_SIGNATURE_PATTERN.search(source_text)
    if not match:
        raise JassPatchError(
            "Missing required 'function main takes nothing returns nothing' declaration."
        )
    return match


def _existing_globals(source_text: str) -> set[str]:
    match = _require_globals_block(source_text)
    block_lines = match.group("block").splitlines()
    return {line.strip() for line in block_lines[1:-1] if line.strip()}


def _existing_function_signatures(source_text: str) -> set[str]:
    _require_main_match(source_text)
    return {
        normalize_for_match(signature.group(0))
        for signature in FUNCTION_SIGNATURE_PATTERN.finditer(source_text)
    }


def _existing_main_calls(source_text: str) -> set[str]:
    main_match = _require_main_match(source_text)
    end_function_pattern = re.compile(
        END_FUNCTION_PATTERN_TEMPLATE, re.IGNORECASE | re.MULTILINE
    )
    end_match = end_function_pattern.search(source_text, pos=main_match.end())
    if not end_match:
        raise JassPatchError("Main function is malformed: missing terminating 'endfunction'.")
    main_block = source_text[main_match.start():end_match.end()]
    body_lines = main_block.splitlines()
    return {line.strip() for line in body_lines[1:-1] if line.strip()}
