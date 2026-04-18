"""GUI entry point for the Warcraft 3 patcher."""

from __future__ import annotations

import sys

import gui_app


def main() -> int:
    """Launch the desktop GUI."""
    return gui_app.main()


if __name__ == "__main__":
    sys.exit(main())
