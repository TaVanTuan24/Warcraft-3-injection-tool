"""Typed data models for the Warcraft 3 trigger injector."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from utils import ConfigValidationError


def make_id(prefix: str) -> str:
    """Create a stable-ish entry identifier."""
    return f"{prefix}_{uuid4().hex}"


@dataclass(slots=True)
class GlobalEntry:
    """A global declaration entry."""

    id: str
    enabled: bool
    source_file: str
    text: str
    name: str = ""


@dataclass(slots=True)
class FunctionEntry:
    """A function block entry."""

    id: str
    enabled: bool
    source_file: str
    text: str
    signature: str
    name: str


@dataclass(slots=True)
class MainCallEntry:
    """A call line that should be inserted into function main."""

    id: str
    enabled: bool
    source_file: str
    text: str
    name: str = ""


@dataclass(slots=True)
class TriggerImportResult:
    """Parsed content from a trigger .j file."""

    source_file: str
    globals_entries: list[GlobalEntry] = field(default_factory=list)
    function_entries: list[FunctionEntry] = field(default_factory=list)
    main_call_entries: list[MainCallEntry] = field(default_factory=list)


@dataclass(slots=True)
class PatchSelection:
    """User-selected patch content."""

    globals_entries: list[GlobalEntry] = field(default_factory=list)
    function_entries: list[FunctionEntry] = field(default_factory=list)
    main_call_entries: list[MainCallEntry] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "PatchSelection":
        """Create an empty selection."""
        return cls()

    def extend_from_import(self, imported: TriggerImportResult) -> None:
        """Append items from a parsed trigger file."""
        self.globals_entries.extend(imported.globals_entries)
        self.function_entries.extend(imported.function_entries)
        self.main_call_entries.extend(imported.main_call_entries)

    def enabled_globals(self) -> list[GlobalEntry]:
        """Return enabled globals only."""
        return [entry for entry in self.globals_entries if entry.enabled]

    def enabled_functions(self) -> list[FunctionEntry]:
        """Return enabled functions only."""
        return [entry for entry in self.function_entries if entry.enabled]

    def enabled_main_calls(self) -> list[MainCallEntry]:
        """Return enabled main calls only."""
        return [entry for entry in self.main_call_entries if entry.enabled]


class InputType(str, Enum):
    """Supported input archive types."""

    MAP_W3X = "w3x"
    MAP_W3M = "w3m"
    CAMPAIGN_W3N = "w3n"

    @property
    def suffix(self) -> str:
        return f".{self.value}"

    @property
    def is_map(self) -> bool:
        return self in (InputType.MAP_W3X, InputType.MAP_W3M)


@dataclass(frozen=True, slots=True)
class ArchiveInputContext:
    """Generic archive input metadata."""

    input_path: Path
    input_type: InputType


@dataclass(frozen=True, slots=True)
class PatchConfig:
    """Text-only patch config used by the JASS patcher."""

    globals_to_add: list[str]
    functions_to_add: list[str]
    init_calls: list[str]


@dataclass(frozen=True, slots=True)
class PatchResult:
    """Summary of a patch operation."""

    added_globals: int
    added_functions: int
    added_init_calls: int
    output_path: Path


@dataclass(frozen=True, slots=True)
class PatchRunOptions:
    """Runtime options for a patch execution."""

    overwrite: bool = False
    keep_temp: bool = False
    verbose: bool = False
    stop_on_first_error: bool = False


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validation outcome for injection/build."""

    is_valid: bool
    issues: list[str]
    warnings: list[str]


@dataclass(slots=True)
class MapSourceContext:
    """Extracted map source context."""

    input_path: Path
    input_type: InputType
    workspace_root: Path
    extracted_dir: Path
    script_path: Path
    script_relative_path: Path
    source_text: str
    script_candidates: tuple[Path, ...] = field(default_factory=tuple)
    script_discovery_warning: str | None = None
    external_listfiles: tuple[Path, ...] = field(default_factory=tuple)
    temp_tracker: object | None = None

    @property
    def war3map_j_path(self) -> Path:
        """Backwards-compatible alias for the detected map script path."""
        return self.script_path


@dataclass(frozen=True, slots=True)
class PatchedSourceResult:
    """Summary of injected source changes before direct archive replacement."""

    effective_selection: PatchSelection
    patch_result: PatchResult
    patched_text: str


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Archive build result."""

    output_path: Path


@dataclass(slots=True)
class CampaignMapEntry:
    """Metadata and patchability status for one embedded campaign map."""

    id: str
    archive_path: str
    map_name: str
    map_type: InputType
    selected: bool = False
    patchable: bool = False
    status: str = "pending"
    message: str = ""


@dataclass(slots=True)
class CampaignContext:
    """Extracted campaign workspace and discovered embedded maps."""

    input_path: Path
    input_type: InputType
    workspace_root: Path
    extracted_dir: Path
    map_entries: list[CampaignMapEntry] = field(default_factory=list)
    external_listfiles: tuple[Path, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CampaignPatchResult:
    """Per-map result produced during campaign patch/build."""

    map_entry_id: str
    archive_path: str
    map_name: str
    selected: bool
    succeeded: bool
    skipped: bool
    failed: bool
    added_globals: int = 0
    added_functions: int = 0
    added_init_calls: int = 0
    duplicate_globals_skipped: int = 0
    duplicate_functions_skipped: int = 0
    duplicate_main_calls_skipped: int = 0
    message: str = ""


@dataclass(frozen=True, slots=True)
class CampaignBuildSummary:
    """Overall campaign patch/build outcome."""

    output_path: Path
    total_maps_found: int
    selected_maps: int
    succeeded_maps: int
    skipped_maps: int
    failed_maps: int
    per_map_results: list[CampaignPatchResult]


class MpqBackendType(str, Enum):
    """Supported MPQ backend families."""

    MPQCLI = "mpqcli"
    MPQEDITOR = "mpqeditor"


@dataclass(frozen=True, slots=True)
class MpqBackendInfo:
    """Metadata about the selected archive backend."""

    name: str
    backend_type: MpqBackendType
    executable: Path
    detected_as: str


def entries_to_json_dict(selection: PatchSelection) -> dict[str, Any]:
    """Serialize a patch selection."""
    return {
        "globals_entries": [entry.__dict__ for entry in selection.globals_entries],
        "function_entries": [entry.__dict__ for entry in selection.function_entries],
        "main_call_entries": [entry.__dict__ for entry in selection.main_call_entries],
    }


def patch_selection_from_dict(payload: dict[str, Any]) -> PatchSelection:
    """Deserialize a patch selection."""
    try:
        globals_entries = [
            GlobalEntry(**entry) for entry in payload.get("globals_entries", [])
        ]
        function_entries = [
            FunctionEntry(**entry) for entry in payload.get("function_entries", [])
        ]
        main_call_entries = [
            MainCallEntry(**entry) for entry in payload.get("main_call_entries", [])
        ]
    except TypeError as exc:
        raise ConfigValidationError(f"Malformed patch preset: {exc}") from exc

    return PatchSelection(
        globals_entries=globals_entries,
        function_entries=function_entries,
        main_call_entries=main_call_entries,
    )
