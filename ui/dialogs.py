"""Dialog helpers for the desktop UI."""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_paths import writable_path


def show_error(parent: QWidget, title: str, message: str) -> None:
    """Show an error dialog."""
    QMessageBox.critical(parent, title, message)


def show_info(parent: QWidget, title: str, message: str) -> None:
    """Show an informational dialog."""
    QMessageBox.information(parent, title, message)


def confirm(parent: QWidget, title: str, message: str) -> bool:
    """Ask the user to confirm an action."""
    return (
        QMessageBox.question(parent, title, message, QMessageBox.Yes | QMessageBox.No)
        == QMessageBox.Yes
    )


def select_open_file(parent: QWidget, title: str, file_filter: str, start_path: str = "") -> str:
    """Show an open-file dialog."""
    path, _ = QFileDialog.getOpenFileName(parent, title, start_path, file_filter)
    return path


def select_open_files(parent: QWidget, title: str, file_filter: str, start_path: str = "") -> list[str]:
    """Show a multi-file picker dialog."""
    paths, _ = QFileDialog.getOpenFileNames(parent, title, start_path, file_filter)
    return paths


def select_save_file(parent: QWidget, title: str, file_filter: str, start_path: str = "") -> str:
    """Show a save-file dialog."""
    path, _ = QFileDialog.getSaveFileName(parent, title, start_path, file_filter)
    return path


def default_preset_path() -> str:
    """Default preset path."""
    return str(writable_path("patch_preset.json"))


class TextEntryDialog(QDialog):
    """Simple monospace text dialog."""

    def __init__(self, parent: QWidget, title: str, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 360)

        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit()
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.editor.setFont(font)
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setPlainText(text)
        layout.addWidget(self.editor)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> str:
        """Return current text."""
        return self.editor.toPlainText()
