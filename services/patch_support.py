"""Shared backend resolution and patch capability checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from models import MpqBackendType, ValidationResult
from mpq_handler import MpqHandler


@dataclass(frozen=True, slots=True)
class PatchBackendCapabilities:
    """Effective patch capabilities for the selected backend."""

    supports_list_entries: bool
    supports_extract_single_file: bool
    supports_delete_entry: bool
    supports_add_entry: bool
    supports_native_replace: bool

    @property
    def can_patch_archive(self) -> bool:
        return (
            self.supports_list_entries
            and self.supports_extract_single_file
            and self.supports_delete_entry
            and self.supports_add_entry
        )

    @property
    def patch_method(self) -> str:
        if self.supports_native_replace:
            return "native replace + delete/add fallback"
        if self.can_patch_archive:
            return "delete + add replacement"
        return "unsupported"


def resolve_patch_backend(
    external_listfiles: Sequence[Path] | None = None,
    log_callback=None,
) -> tuple[MpqHandler, PatchBackendCapabilities]:
    """Resolve the backend used for direct patching."""
    log = log_callback or (lambda _severity, _message: None)
    preferred_backend_type = MpqBackendType.MPQEDITOR if external_listfiles else None
    handler = MpqHandler.auto_detect(preferred_backend_type=preferred_backend_type)
    capabilities = get_patch_capabilities(handler)
    log("INFO", f"Backend selected: {describe_backend(handler)}")
    log("INFO", f"Backend capabilities: {format_patch_capabilities(capabilities)}")
    return handler, capabilities


def get_patch_capabilities(handler: MpqHandler) -> PatchBackendCapabilities:
    """Return normalized patch capabilities for the selected backend."""
    return PatchBackendCapabilities(
        supports_list_entries=handler.supports_list_entries(),
        supports_extract_single_file=handler.supports_extract_single_file(),
        supports_delete_entry=handler.supports_delete_entry(),
        supports_add_entry=handler.supports_add_entry(),
        supports_native_replace=handler.supports_replace_entry(),
    )


def validate_patch_backend_support(
    handler: MpqHandler,
    capabilities: PatchBackendCapabilities | None = None,
) -> ValidationResult:
    """Validate whether the backend can patch an archive using effective replacement."""
    caps = capabilities or get_patch_capabilities(handler)
    if caps.can_patch_archive:
        return ValidationResult(is_valid=True, issues=[], warnings=[])

    missing: list[str] = []
    if not caps.supports_list_entries:
        missing.append("list entries")
    if not caps.supports_extract_single_file:
        missing.append("extract single file")
    if not caps.supports_delete_entry:
        missing.append("delete archive entry")
    if not caps.supports_add_entry:
        missing.append("add archive entry")

    return ValidationResult(
        is_valid=False,
        issues=[
            "A direct-replace MPQ backend is required before patching. "
            f"Backend '{handler.backend.name}' is missing: {', '.join(missing)}."
        ],
        warnings=[],
    )


def describe_backend(handler: MpqHandler) -> str:
    """Create a short backend description for logs."""
    backend = handler.backend
    return (
        f"{backend.name} "
        f"(type={backend.backend_type.value}, detected_as={backend.detected_as}, executable={backend.executable})"
    )


def format_patch_capabilities(capabilities: PatchBackendCapabilities) -> str:
    """Format capability flags for debug logs."""
    return (
        "list={list_entries}, extract_single={extract_single}, delete={delete_entry}, "
        "add={add_entry}, native_replace={native_replace}, can_patch={can_patch}, method={method}"
    ).format(
        list_entries=capabilities.supports_list_entries,
        extract_single=capabilities.supports_extract_single_file,
        delete_entry=capabilities.supports_delete_entry,
        add_entry=capabilities.supports_add_entry,
        native_replace=capabilities.supports_native_replace,
        can_patch=capabilities.can_patch_archive,
        method=capabilities.patch_method,
    )
