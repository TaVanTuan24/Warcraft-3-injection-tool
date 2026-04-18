"""Patch preset serialization helpers."""

from __future__ import annotations

import json
from pathlib import Path

from models import PatchSelection, entries_to_json_dict, patch_selection_from_dict
from utils import ConfigValidationError


def load_patch_preset(path: Path) -> PatchSelection:
    """Load a patch selection preset from disk."""
    if not path.is_file():
        raise FileNotFoundError(f"Patch preset not found: {path}")
    return load_patch_preset_from_text(path.read_text(encoding="utf-8"))


def load_patch_preset_from_text(text: str) -> PatchSelection:
    """Load a patch preset from raw JSON text."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(
            f"Malformed patch preset JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from exc

    if not isinstance(payload, dict):
        raise ConfigValidationError("Patch preset root must be a JSON object.")
    return patch_selection_from_dict(payload)


def dump_patch_preset(selection: PatchSelection) -> str:
    """Serialize a patch selection into JSON."""
    return json.dumps(entries_to_json_dict(selection), indent=2)


def save_patch_preset(path: Path, selection: PatchSelection) -> None:
    """Save a patch preset to disk."""
    path.write_text(dump_patch_preset(selection) + "\n", encoding="utf-8")
