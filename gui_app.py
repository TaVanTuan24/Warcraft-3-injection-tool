"""Desktop GUI entry point for the Warcraft 3 map patcher."""

from __future__ import annotations

import sys


def main() -> int:
    """Launch the desktop UI."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "PySide6 is required for the desktop UI. Install dependencies with "
            "'pip install -r requirements.txt'.",
            file=sys.stderr,
        )
        return 1

    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Warcraft 3 Trigger Injector")
    app.setOrganizationName("LocalTooling")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
