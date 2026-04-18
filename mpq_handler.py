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
from utils import ArchiveProcessingError


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


class BaseMpqBackendAdapter:
    """Backend-specific command builder."""

    backend_type: MpqBackendType
    backend_name: str

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
    ) -> tuple[BackendCommand, ...]:
        return (
            BackendCommand(
                args=(str(backend.executable), "new", str(output_path))
            ),
            BackendCommand(
                args=(
                    str(backend.executable),
                    "add",
                    str(output_path),
                    "*",
                    "/auto",
                    "/r",
                ),
                cwd=source_dir,
            ),
        )


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

    def rebuild_archive(self, source_dir: Path, output_path: Path) -> None:
        """Rebuild a supported Warcraft 3 archive from an extracted directory."""
        if not source_dir.is_dir():
            raise ArchiveProcessingError(
                f"Cannot rebuild archive: source directory does not exist: {source_dir}"
            )

        commands = self._adapter.build_rebuild_commands(
            backend=self.backend,
            source_dir=source_dir,
            output_path=output_path,
        )
        self._run_backend_commands(
            commands=commands,
            error_context="Failed to rebuild the patched map archive.",
        )
        if not output_path.is_file():
            raise ArchiveProcessingError(
                f"MPQ backend completed without creating the expected output file: {output_path}"
            )

    def rebuild_map(self, source_dir: Path, output_path: Path) -> None:
        """Backwards-compatible wrapper for map rebuild."""
        self.rebuild_archive(source_dir=source_dir, output_path=output_path)

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
