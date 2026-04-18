"""Helpers for source-mode vs PyInstaller-bundled runtime paths."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return whether the app is running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """Return the directory containing bundled read-only resources."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parent


def app_root() -> Path:
    """Return the user-visible application directory."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    """Resolve a bundled resource path that works in source and frozen mode."""
    return bundle_root().joinpath(*parts)


def writable_path(*parts: str) -> Path:
    """Resolve a writable path located beside the source tree or built executable."""
    return app_root().joinpath(*parts)
