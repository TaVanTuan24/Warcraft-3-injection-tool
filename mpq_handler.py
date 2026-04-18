"""Archive backend adapter for Warcraft 3 MPQ map extraction and rebuild."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app_paths import app_root, bundle_root
from models import MpqBackendInfo, MpqBackendType
from utils import ArchiveProcessingError, cleanup_workspace, create_temp_workspace, to_archive_path


LOGGER = logging.getLogger(__name__)


SUPPORTED_BACKEND_CANDIDATES: tuple[tuple[str, MpqBackendType, str], ...] = (
    ("mpqcli", MpqBackendType.MPQCLI, "mpqcli"),
    ("mpqcli.exe", MpqBackendType.MPQCLI, "mpqcli"),
    ("MPQEditor", MpqBackendType.MPQEDITOR, "MPQEditor"),
    ("MPQEditor.exe", MpqBackendType.MPQEDITOR, "MPQEditor"),
)


@dataclass(frozen=True, slots=True)
class BackendCommand:
    """Concrete backend command invocation details."""

    args: tuple[str, ...]
    cwd: Path | None = None


@dataclass(frozen=True, slots=True)
class ArchiveBuildEntry:
    """One file that should be written into the rebuilt archive."""

    source_path: Path
    archive_path: str


class BaseMpqBackendAdapter:
    """Backend-specific command builder."""

    backend_type: MpqBackendType
    backend_name: str
    supports_list_entries = False
    supports_extract_single_file = False
    supports_delete_entry = False
    supports_add_entry = False
    supports_replace_entry = False

    def build_test_command(self, backend: MpqBackendInfo) -> BackendCommand:
        """Return a lightweight command that validates backend execution."""
        raise NotImplementedError

    def build_extract_command(
        self,
        backend: MpqBackendInfo,
        input_path: Path,
        destination_dir: Path,
    ) -> BackendCommand:
        """Return the backend command used to extract a map archive."""
        raise NotImplementedError

    def build_rebuild_commands(
        self,
        backend: MpqBackendInfo,
        source_dir: Path,
        output_path: Path,
        archive_entries: Sequence[ArchiveBuildEntry],
    ) -> tuple[BackendCommand, ...]:
        """Return the backend commands used to rebuild a map archive."""
        raise NotImplementedError


class MpqCliBackendAdapter(BaseMpqBackendAdapter):
    """Adapter for the mpqcli backend."""

    backend_type = MpqBackendType.MPQCLI
    backend_name = "mpqcli"

    def build_test_command(self, backend: MpqBackendInfo) -> BackendCommand:
        return BackendCommand(args=(str(backend.executable), "version"))

    def build_extract_command(
        self,
        backend: MpqBackendInfo,
        input_path: Path,
        destination_dir: Path,
    ) -> BackendCommand:
        return BackendCommand(
            args=(
                str(backend.executable),
                "extract",
                "-o",
                str(destination_dir),
                str(input_path),
            )
        )

    def build_rebuild_commands(
        self,
        backend: MpqBackendInfo,
        source_dir: Path,
        output_path: Path,
        archive_entries: Sequence[ArchiveBuildEntry],
    ) -> tuple[BackendCommand, ...]:
        return (
            BackendCommand(
                args=(
                    str(backend.executable),
                    "create",
                    str(source_dir),
                    str(output_path),
                )
            ),
        )


class MpqEditorBackendAdapter(BaseMpqBackendAdapter):
    """Adapter for Ladik's MPQ Editor command-line mode."""

    backend_type = MpqBackendType.MPQEDITOR
    backend_name = "MPQEditor"
    supports_list_entries = True
    supports_extract_single_file = True
    supports_delete_entry = True
    supports_add_entry = True
    supports_replace_entry = True

    def build_test_command(self, backend: MpqBackendInfo) -> BackendCommand:
        return BackendCommand(args=(str(backend.executable), "version"))

    def build_extract_command(
        self,
        backend: MpqBackendInfo,
        input_path: Path,
        destination_dir: Path,
    ) -> BackendCommand:
        return BackendCommand(
            args=(
                str(backend.executable),
                "extract",
                str(input_path),
                "*",
                str(destination_dir),
                "/fp",
            )
        )

    def build_rebuild_commands(
        self,
        backend: MpqBackendInfo,
        source_dir: Path,
        output_path: Path,
        archive_entries: Sequence[ArchiveBuildEntry],
    ) -> tuple[BackendCommand, ...]:
        commands: list[BackendCommand] = [
            BackendCommand(args=(str(backend.executable), "new", str(output_path))),
            BackendCommand(
                args=(
                    str(backend.executable),
                    "add",
                    str(output_path),
                    "*",
                    ".",
                    "/auto",
                    "/r",
                ),
                cwd=source_dir,
            ),
        ]

        rename_candidates = [
            entry
            for entry in archive_entries
            if not entry.archive_path.startswith("(")
        ]
        chunk_size = 500
        for index in range(0, len(rename_candidates), chunk_size):
            chunk = rename_candidates[index:index + chunk_size]
            script_path = source_dir.parent / f"__mpqeditor_rebuild_rename_{index // chunk_size:03d}.txt"
            script_lines = []
            for entry in chunk:
                mpqeditor_path = _to_mpqeditor_archive_path(entry.archive_path)
                script_lines.append(
                    f'rename "{output_path}" ".\\{mpqeditor_path}" "{mpqeditor_path}"'
                )
            script_lines.extend(["close", "exit"])
            script_path.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
            commands.append(
                BackendCommand(
                    args=(str(backend.executable), "/console", str(script_path))
                )
            )

        return tuple(commands)


BACKEND_ADAPTERS: dict[MpqBackendType, BaseMpqBackendAdapter] = {
    MpqBackendType.MPQCLI: MpqCliBackendAdapter(),
    MpqBackendType.MPQEDITOR: MpqEditorBackendAdapter(),
}


class MpqHandler:
    """Safe adapter around an external MPQ CLI backend."""

    def __init__(self, backend: MpqBackendInfo) -> None:
        self.backend = backend
        self._adapter = BACKEND_ADAPTERS[backend.backend_type]

    @classmethod
    def auto_detect(
        cls,
        preferred_backend_type: MpqBackendType | None = None,
    ) -> "MpqHandler":
        """Detect and validate a supported MPQ backend on PATH."""
        candidates = list(SUPPORTED_BACKEND_CANDIDATES)
        if preferred_backend_type is not None:
            candidates.sort(key=lambda candidate: candidate[1] != preferred_backend_type)

        searched_names = [candidate[0] for candidate in candidates]
        detection_failures: list[str] = []

        for executable_name, backend_type, backend_name in candidates:
            resolved = cls._resolve_backend_executable(executable_name)
            if not resolved:
                continue

            backend = MpqBackendInfo(
                name=backend_name,
                backend_type=backend_type,
                executable=Path(resolved).resolve(),
                detected_as=executable_name,
            )
            handler = cls(backend)

            try:
                handler.test_backend()
            except ArchiveProcessingError as exc:
                detection_failures.append(
                    f"{executable_name} -> {backend.executable}: {exc}"
                )
                continue

            LOGGER.debug(
                "Detected MPQ backend: type=%s executable=%s detected_as=%s",
                backend.backend_type.value,
                backend.executable,
                backend.detected_as,
            )
            return handler

        searched = ", ".join(searched_names)
        message = (
            f"No supported MPQ backend found or validated on PATH. Searched executable names: "
            f"{searched}."
        )
        if detection_failures:
            message = f"{message} Detected candidates failed validation: {'; '.join(detection_failures)}"
        else:
            message = (
                f"{message} Install a supported backend and ensure one of those executable names "
                f"is available on PATH."
            )

        raise ArchiveProcessingError(message)

    @staticmethod
    def _resolve_backend_executable(executable_name: str) -> str | None:
        """Resolve a backend executable from the app folder, bundle, or PATH."""
        search_roots: list[Path] = []
        for root in (app_root(), bundle_root()):
            if root not in search_roots:
                search_roots.append(root)

        for root in search_roots:
            candidate = (root / executable_name).resolve()
            if candidate.is_file():
                return str(candidate)

        return shutil.which(executable_name)

    def test_backend(self) -> None:
        """Verify that the selected backend executable launches successfully."""
        test_command = self._adapter.build_test_command(self.backend)
        self._run_backend_command(
            command=test_command,
            error_context=(
                f"Failed to validate MPQ backend '{self.backend.name}' "
                f"({self.backend.executable})."
            ),
        )

    def extract_archive(
        self,
        input_path: Path,
        destination_dir: Path,
        external_listfiles: Sequence[Path] | None = None,
    ) -> None:
        """Extract a supported Warcraft 3 archive into a destination directory."""
        self._validate_input_archive_path(input_path)
        destination_dir.mkdir(parents=True, exist_ok=True)
        resolved_listfiles = self._resolve_external_listfiles(external_listfiles)

        if resolved_listfiles:
            if self.backend.backend_type is not MpqBackendType.MPQEDITOR:
                raise ArchiveProcessingError(
                    "External listfiles require the MPQEditor backend. Install MPQEditor or "
                    "remove the configured listfiles."
                )
            command = self._build_mpqeditor_extract_with_listfiles_command(
                input_path=input_path,
                destination_dir=destination_dir,
                external_listfiles=resolved_listfiles,
            )
        else:
            command = self._adapter.build_extract_command(
                backend=self.backend,
                input_path=input_path,
                destination_dir=destination_dir,
            )

        error_context = (
            "Failed to extract the map archive. The map may be unreadable, unsupported, or protected."
        )
        self._run_backend_command(
            command=command,
            error_context=error_context,
        )
        if not any(destination_dir.iterdir()):
            raise ArchiveProcessingError(
                "Archive extraction produced no files. The archive may be unreadable, "
                "unsupported, or protected."
            )

    def extract_map(
        self,
        input_path: Path,
        destination_dir: Path,
        external_listfiles: Sequence[Path] | None = None,
    ) -> None:
        """Backwards-compatible wrapper for map extraction."""
        self.extract_archive(
            input_path=input_path,
            destination_dir=destination_dir,
            external_listfiles=external_listfiles,
        )

    def supports_list_entries(self) -> bool:
        """Return whether the backend can enumerate archive entries."""
        return bool(self._adapter.supports_list_entries)

    def supports_extract_single_file(self) -> bool:
        """Return whether the backend can extract a single archive entry."""
        return bool(self._adapter.supports_extract_single_file)

    def supports_delete_entry(self) -> bool:
        """Return whether the backend can delete a single archive entry."""
        return bool(self._adapter.supports_delete_entry)

    def supports_add_entry(self) -> bool:
        """Return whether the backend can add a single archive entry."""
        return bool(self._adapter.supports_add_entry)

    def supports_replace_entry(self) -> bool:
        """Return whether the backend can replace a single archive entry."""
        return bool(self._adapter.supports_replace_entry)

    def supports_fast_replace(self) -> bool:
        """Return whether the backend supports the fast script-replace workflow."""
        return (
            self.supports_list_entries()
            and self.supports_delete_entry()
            and self.supports_add_entry()
            and self.supports_replace_entry()
        )

    def list_archive_entries(self, archive_path: Path) -> tuple[str, ...]:
        """List normalized archive entry paths."""
        self._validate_input_archive_path(archive_path)
        if not self.supports_list_entries():
            raise ArchiveProcessingError(
                f"Backend '{self.backend.name}' does not support archive entry listing."
            )

        if self.backend.backend_type is MpqBackendType.MPQEDITOR:
            temp_dir = create_temp_workspace("mpq_list_", logger=LOGGER)
            output_path = temp_dir / "entries.txt"
            try:
                command = BackendCommand(
                    args=(
                        str(self.backend.executable),
                        "list",
                        str(archive_path),
                        "*",
                        str(output_path),
                    )
                )
                self._run_backend_command(
                    command=command,
                    error_context="Failed to list archive entries.",
                )
                if not output_path.is_file():
                    raise ArchiveProcessingError(
                        f"Backend did not produce an archive listing for {archive_path}."
                    )
                entries = []
                for raw_line in output_path.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines():
                    entry = _normalize_archive_entry_path(raw_line)
                    if entry:
                        entries.append(entry)
                return tuple(entries)
            finally:
                cleanup_workspace(temp_dir, keep=False, logger=LOGGER)

        raise ArchiveProcessingError(
            f"Archive entry listing is not implemented for backend '{self.backend.name}'."
        )

    def extract_file_from_archive(
        self,
        archive_path: Path,
        archive_entry_path: str,
        output_path: Path,
    ) -> None:
        """Extract one archive entry into a local file path."""
        self._validate_input_archive_path(archive_path)
        if not self.supports_extract_single_file():
            raise ArchiveProcessingError(
                f"Backend '{self.backend.name}' does not support single-file extraction."
            )

        normalized_entry = _normalize_archive_entry_path(archive_entry_path)
        if not normalized_entry:
            raise ArchiveProcessingError("Archive entry path is required for single-file extraction.")

        if self.backend.backend_type is MpqBackendType.MPQEDITOR:
            temp_dir = create_temp_workspace("mpq_extract_single_", logger=LOGGER)
            try:
                command = BackendCommand(
                    args=(
                        str(self.backend.executable),
                        "extract",
                        str(archive_path),
                        _to_mpqeditor_archive_path(normalized_entry),
                        str(temp_dir),
                        "/fp",
                    )
                )
                self._run_backend_command(
                    command=command,
                    error_context="Failed to extract the requested archive entry.",
                )
                extracted_path = temp_dir / Path(normalized_entry)
                if not extracted_path.is_file():
                    raise ArchiveProcessingError(
                        f"Backend did not extract the requested archive entry: {normalized_entry}"
                    )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(extracted_path, output_path)
                return
            finally:
                cleanup_workspace(temp_dir, keep=False, logger=LOGGER)

        raise ArchiveProcessingError(
            f"Single-file extraction is not implemented for backend '{self.backend.name}'."
        )

    def delete_file_in_archive(self, archive_path: Path, archive_entry_path: str) -> None:
        """Delete one entry from an archive."""
        self._validate_input_archive_path(archive_path)
        if not self.supports_delete_entry():
            raise ArchiveProcessingError(
                f"Backend '{self.backend.name}' does not support deleting archive entries."
            )

        normalized_entry = _normalize_archive_entry_path(archive_entry_path)
        if not normalized_entry:
            raise ArchiveProcessingError("Archive entry path is required for delete.")

        if self.backend.backend_type is MpqBackendType.MPQEDITOR:
            command = BackendCommand(
                args=(
                    str(self.backend.executable),
                    "delete",
                    str(archive_path),
                    _to_mpqeditor_archive_path(normalized_entry),
                )
            )
            self._run_backend_command(
                command=command,
                error_context=f"Failed to delete archive entry '{normalized_entry}'.",
            )
            return

        raise ArchiveProcessingError(
            f"Archive entry deletion is not implemented for backend '{self.backend.name}'."
        )

    def add_file_to_archive(
        self,
        archive_path: Path,
        archive_entry_path: str,
        local_file_path: Path,
    ) -> None:
        """Add one local file into an archive entry path."""
        self._validate_input_archive_path(archive_path)
        if not self.supports_add_entry():
            raise ArchiveProcessingError(
                f"Backend '{self.backend.name}' does not support adding archive entries."
            )
        if not local_file_path.is_file():
            raise ArchiveProcessingError(f"Local file for archive add was not found: {local_file_path}")

        normalized_entry = _normalize_archive_entry_path(archive_entry_path)
        if not normalized_entry:
            raise ArchiveProcessingError("Archive entry path is required for add.")

        if self.backend.backend_type is MpqBackendType.MPQEDITOR:
            command = BackendCommand(
                args=(
                    str(self.backend.executable),
                    "add",
                    str(archive_path),
                    str(local_file_path),
                    _to_mpqeditor_archive_path(normalized_entry),
                )
            )
            self._run_backend_command(
                command=command,
                error_context=f"Failed to add archive entry '{normalized_entry}'.",
            )
            return

        raise ArchiveProcessingError(
            f"Archive entry add is not implemented for backend '{self.backend.name}'."
        )

    def replace_file_in_archive(
        self,
        archive_path: Path,
        archive_entry_path: str,
        local_file_path: Path,
    ) -> None:
        """Replace one archive entry with a local file."""
        self._validate_input_archive_path(archive_path)
        if not self.supports_replace_entry():
            raise ArchiveProcessingError(
                f"Backend '{self.backend.name}' does not support replacing archive entries."
            )
        if not local_file_path.is_file():
            raise ArchiveProcessingError(
                f"Patched script file does not exist: {local_file_path}"
            )

        normalized_entry = _normalize_archive_entry_path(archive_entry_path)
        entries = set(self.list_archive_entries(archive_path))
        if normalized_entry not in entries:
            raise ArchiveProcessingError(
                f"Archive entry not found for fast replace: {normalized_entry}"
            )

        self.delete_file_in_archive(archive_path, normalized_entry)
        self.add_file_to_archive(archive_path, normalized_entry, local_file_path)

        updated_entries = set(self.list_archive_entries(archive_path))
        if normalized_entry not in updated_entries:
            raise ArchiveProcessingError(
                f"Fast replace did not restore the expected archive entry: {normalized_entry}"
            )

    def rebuild_archive(self, source_dir: Path, output_path: Path, log_callback=None) -> None:
        """Rebuild a supported Warcraft 3 archive from an extracted directory."""
        log = log_callback or (lambda _severity, _message: None)
        if not source_dir.is_dir():
            raise ArchiveProcessingError(
                f"Cannot rebuild archive: source directory does not exist: {source_dir}"
            )

        archive_entries = self._collect_archive_entries(source_dir, log)
        if not archive_entries:
            raise ArchiveProcessingError(
                f"Cannot rebuild archive: no valid files were found under {source_dir}"
            )
        if self.backend.backend_type is MpqBackendType.MPQEDITOR:
            rename_candidates = len(
                [entry for entry in archive_entries if not entry.archive_path.startswith("(")]
            )
            log(
                "INFO",
                f"MPQEditor rebuild will normalize {rename_candidates} archive path(s) after import.",
            )

        commands = self._adapter.build_rebuild_commands(
            backend=self.backend,
            source_dir=source_dir,
            output_path=output_path,
            archive_entries=archive_entries,
        )
        self._run_backend_commands(
            commands=commands,
            error_context="Failed to rebuild the patched map archive.",
        )
        if not output_path.is_file():
            raise ArchiveProcessingError(
                f"MPQ backend completed without creating the expected output file: {output_path}"
            )

    def rebuild_map(self, source_dir: Path, output_path: Path, log_callback=None) -> None:
        """Backwards-compatible wrapper for map rebuild."""
        self.rebuild_archive(
            source_dir=source_dir,
            output_path=output_path,
            log_callback=log_callback,
        )

    def _validate_input_archive_path(self, input_path: Path) -> None:
        """Validate the input archive path."""
        if not input_path.is_file():
            raise FileNotFoundError(f"Input file not found: {input_path}")
        if input_path.suffix.lower() not in {".w3x", ".w3m", ".w3n"}:
            raise ArchiveProcessingError(
                f"Unsupported input file type '{input_path.suffix}'. Supported archive types: .w3x, .w3m, .w3n."
            )

    def _resolve_external_listfiles(
        self,
        external_listfiles: Sequence[Path] | None,
    ) -> tuple[Path, ...]:
        """Validate and normalize any user-supplied MPQ listfiles."""
        if not external_listfiles:
            return ()

        resolved_paths: list[Path] = []
        seen_paths: set[Path] = set()
        for path in external_listfiles:
            resolved = Path(path).expanduser().resolve()
            if resolved in seen_paths:
                continue
            if not resolved.is_file():
                raise ArchiveProcessingError(f"Listfile not found: {resolved}")
            seen_paths.add(resolved)
            resolved_paths.append(resolved)
        return tuple(resolved_paths)

    def _build_mpqeditor_extract_with_listfiles_command(
        self,
        input_path: Path,
        destination_dir: Path,
        external_listfiles: Sequence[Path],
    ) -> BackendCommand:
        """Build an MPQEditor console script that opens the archive with merged listfiles."""
        combined_listfile = destination_dir.parent / "__external_listfile__.txt"
        merged_entries = self._merge_listfile_entries(external_listfiles)
        if not merged_entries:
            raise ArchiveProcessingError(
                "The selected listfile(s) did not contain any usable file names."
            )
        combined_listfile.write_text("\n".join(merged_entries) + "\n", encoding="utf-8")

        script_path = destination_dir.parent / "__mpqeditor_extract_script.txt"
        script_lines = [
            f'open "{input_path}" "{combined_listfile}"',
            f'extract "{input_path}" "*" "{destination_dir}" /fp',
            f'close "{input_path}"',
            "exit",
        ]
        script_path.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
        return BackendCommand(
            args=(str(self.backend.executable), "/console", str(script_path))
        )

    def _merge_listfile_entries(self, external_listfiles: Sequence[Path]) -> list[str]:
        """Merge multiple listfiles into one de-duplicated file for MPQEditor."""
        merged_entries: list[str] = []
        seen_entries: set[str] = set()
        for listfile_path in external_listfiles:
            contents = listfile_path.read_text(encoding="utf-8", errors="replace")
            for raw_line in contents.splitlines():
                entry = raw_line.strip()
                if not entry:
                    continue
                entry_key = entry.lower()
                if entry_key in seen_entries:
                    continue
                seen_entries.add(entry_key)
                merged_entries.append(entry)
        return merged_entries

    def _collect_archive_entries(
        self,
        source_dir: Path,
        log_callback,
    ) -> tuple[ArchiveBuildEntry, ...]:
        """Build a normalized archive file manifest from a workspace root."""
        log_callback("INFO", f"Rebuild workspace root: {source_dir}")

        entries: list[ArchiveBuildEntry] = []
        for source_path in sorted(path.resolve() for path in source_dir.rglob("*") if path.is_file()):
            try:
                archive_path = to_archive_path(source_path, source_dir)
            except ValueError as exc:
                log_callback("WARNING", f"Skipped invalid archive entry: {exc}")
                continue

            log_callback("DEBUG", f"Source file: {source_path}")
            log_callback("DEBUG", f"Computed archive path: {archive_path}")
            entries.append(ArchiveBuildEntry(source_path=source_path, archive_path=archive_path))

        log_callback("INFO", f"Total files queued for archive rebuild: {len(entries)}")
        return tuple(entries)

    def _run_backend_commands(
        self,
        commands: tuple[BackendCommand, ...],
        error_context: str,
    ) -> None:
        """Run a sequence of backend commands."""
        for command in commands:
            self._run_backend_command(command=command, error_context=error_context)

    def _run_backend_command(
        self,
        command: BackendCommand,
        error_context: str,
    ) -> None:
        """Run a backend command and convert failures into safe user-facing errors."""
        env = dict(os.environ)
        try:
            completed = subprocess.run(
                list(command.args),
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(command.cwd) if command.cwd else None,
            )
        except OSError as exc:
            raise ArchiveProcessingError(
                self._format_backend_error(
                    error_context=error_context,
                    command=command,
                    backend_output=str(exc),
                )
            ) from exc

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        LOGGER.debug("Backend command: %s", self._format_command_for_log(command))
        LOGGER.debug("Backend stdout: %s", stdout)
        LOGGER.debug("Backend stderr: %s", stderr)

        if completed.returncode != 0:
            backend_output = stderr or stdout or f"Exit code: {completed.returncode}"
            raise ArchiveProcessingError(
                self._format_backend_error(
                    error_context=error_context,
                    command=command,
                    backend_output=backend_output,
                )
            )

    def _format_backend_error(
        self,
        error_context: str,
        command: BackendCommand,
        backend_output: str,
    ) -> str:
        """Create a detailed backend failure message."""
        parts = [
            error_context,
            (
                f"Backend: {self.backend.name} "
                f"(type={self.backend.backend_type.value}, detected_as={self.backend.detected_as})"
            ),
            f"Executable: {self.backend.executable}",
            f"Command: {self._format_command_for_log(command)}",
        ]
        if command.cwd:
            parts.append(f"Working directory: {command.cwd}")
        if backend_output:
            parts.append(f"Backend output: {backend_output}")
        return " ".join(parts)

    def _format_command_for_log(self, command: BackendCommand) -> str:
        """Format a command for logs and error messages."""
        return subprocess.list2cmdline(list(command.args))


def _to_mpqeditor_archive_path(archive_path: str) -> str:
    """Convert a normalized archive path into MPQEditor's preferred separator style."""
    return archive_path.replace("/", "\\")


def _normalize_archive_entry_path(archive_entry_path: str) -> str:
    """Normalize an archive entry path for internal comparisons and commands."""
    normalized = archive_entry_path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    while normalized.startswith("/"):
        normalized = normalized[1:]
    if normalized in {"", ".", "./", ".\\"}:
        return ""
    return normalized
