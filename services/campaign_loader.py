"""Campaign loading and embedded-map discovery services."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from models import CampaignContext, CampaignMapEntry, InputType, make_id
from mpq_handler import MpqHandler
from services.input_detector import detect_input_type
from services.map_loader import dispose_map_source, load_map_source
from utils import ArchiveProcessingError, cleanup_workspace, create_temp_workspace


def load_campaign_source(
    input_campaign: Path,
    external_listfiles: Sequence[Path] | None = None,
    progress_callback=None,
    log_callback=None,
) -> CampaignContext:
    """Extract a readable custom campaign archive into a temp workspace."""
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)
    resolved_listfiles = tuple(Path(path).expanduser().resolve() for path in (external_listfiles or ()))

    input_type = detect_input_type(input_campaign)
    if input_type is not InputType.CAMPAIGN_W3N:
        raise ArchiveProcessingError(
            f"Unsupported campaign input type '{input_campaign.suffix}'. Only .w3n campaigns are supported."
        )

    log("INFO", "Detected input type: campaign (.w3n)")
    log("INFO", "Opening campaign archive.")
    handler = MpqHandler.auto_detect()
    workspace_root = create_temp_workspace("war3campaign_load_", logger=_TempLogger(log))
    extracted_dir = workspace_root / "campaign_contents"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    progress("opening campaign")
    try:
        handler.extract_archive(input_path=input_campaign, destination_dir=extracted_dir)
        if not any(extracted_dir.rglob("*")):
            raise ArchiveProcessingError(
                "Campaign extraction produced no files. The campaign may be unreadable, unsupported, or protected."
            )
        return CampaignContext(
            input_path=input_campaign,
            input_type=input_type,
            workspace_root=workspace_root,
            extracted_dir=extracted_dir,
            external_listfiles=resolved_listfiles,
        )
    except Exception:
        cleanup_workspace(workspace_root, keep=False, logger=_TempLogger(log))
        raise


def list_campaign_maps(
    campaign_context: CampaignContext,
    progress_callback=None,
    log_callback=None,
) -> list[CampaignMapEntry]:
    """Deep-scan embedded maps inside an extracted campaign workspace."""
    progress = progress_callback or (lambda _step: None)
    log = log_callback or (lambda _severity, _message: None)

    progress("scanning campaign contents")
    log("INFO", "Scanning campaign contents.")
    map_paths = sorted(
        path
        for path in campaign_context.extracted_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".w3x", ".w3m"}
    )
    log("INFO", f"Found {len(map_paths)} embedded maps.")

    entries: list[CampaignMapEntry] = []
    for index, map_path in enumerate(map_paths, start=1):
        relative_path = map_path.relative_to(campaign_context.extracted_dir).as_posix()
        map_type = detect_input_type(map_path)
        progress(f"scanning map {index}/{len(map_paths)}")
        log("INFO", f"Scanning embedded map {index}/{len(map_paths)}: {relative_path}")
        entry = CampaignMapEntry(
            id=make_id("campaign_map"),
            archive_path=relative_path,
            map_name=map_path.name,
            map_type=map_type,
            selected=False,
            patchable=False,
            status="pending",
            message="Scanning...",
        )
        try:
            map_context = load_map_source(
                input_war3_archive=map_path,
                external_listfiles=campaign_context.external_listfiles,
                progress_callback=progress_callback,
                log_callback=log_callback,
            )
        except Exception as exc:
            entry.patchable = False
            entry.selected = False
            entry.status = "error"
            entry.message = str(exc)
        else:
            entry.patchable = True
            entry.selected = True
            entry.status = "ready"
            entry.message = "Patchable"
            dispose_map_source(map_context, keep=False, log_callback=log_callback)
        entries.append(entry)

    if not entries:
        log("WARNING", "No embedded .w3x/.w3m maps were found in the campaign.")
    campaign_context.map_entries = entries
    return entries


def dispose_campaign_source(
    campaign_context: CampaignContext,
    keep: bool = False,
    log_callback=None,
) -> None:
    """Clean up an extracted campaign workspace."""
    log = log_callback or (lambda _severity, _message: None)
    cleanup_workspace(campaign_context.workspace_root, keep=keep, logger=_TempLogger(log))


class _TempLogger:
    def __init__(self, callback) -> None:
        self._callback = callback

    def info(self, message: str, *args: object) -> None:
        self._callback("INFO", message % args if args else message)

    def debug(self, message: str, *args: object) -> None:
        self._callback("INFO", message % args if args else message)

    def warning(self, message: str, *args: object) -> None:
        self._callback("WARNING", message % args if args else message)
