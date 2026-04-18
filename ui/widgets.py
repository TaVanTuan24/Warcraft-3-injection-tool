"""Reusable widgets for the trigger injector UI."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


MONOSPACE_FONT_FAMILY = "Consolas"
DEFAULT_OUTER_MARGIN = 12
DEFAULT_SECTION_SPACING = 10
DEFAULT_BUTTON_SPACING = 8


class DropLineEdit(QPlainTextEdit):
    """Single-line drop target for file paths."""

    pathDropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(38)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(6)
        self.setPlaceholderText("Select or drop a file path.")

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        urls = event.mimeData().urls()
        if not urls:
            return
        local_path = urls[0].toLocalFile()
        if local_path:
            self.setPlainText(local_path)
            self.pathDropped.emit(local_path)
            event.acceptProposedAction()

    def text(self) -> str:
        """Return current text."""
        return self.toPlainText().strip()

    def setText(self, value: str) -> None:
        """Set current text."""
        self.setPlainText(value)


class MonospaceTextEdit(QPlainTextEdit):
    """Monospace plain text editor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        font = QFont(MONOSPACE_FONT_FAMILY)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMinimumHeight(140)


class LogPanel(QPlainTextEdit):
    """Scrollable log output with conditional auto-scroll."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont(MONOSPACE_FONT_FAMILY, 10))
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setUndoRedoEnabled(False)
        self.document().setDocumentMargin(8)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMinimumHeight(200)

    def append_log(self, severity: str, message: str) -> None:
        """Append a log line."""
        scrollbar = self.verticalScrollBar()
        previous_value = scrollbar.value()
        should_autoscroll = scrollbar.maximum() - scrollbar.value() <= 24
        line = f"[{severity.upper():7}] {message}"
        self.appendPlainText(line)
        if should_autoscroll:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(previous_value)


@dataclass
class EntryItemPayload:
    """Payload shown in an editable list."""

    entry_id: str
    title: str
    subtitle: str
    enabled: bool


class EntryListEditor(QWidget):
    """List and editor pair for patch entries."""

    changed = Signal()
    selectionChanged = Signal(str)
    addRequested = Signal()
    removeRequested = Signal()
    moveUpRequested = Signal()
    moveDownRequested = Signal()
    currentTextChanged = Signal(str)
    currentEnabledChanged = Signal(bool)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.header = QLabel(title)
        self.header.setObjectName("sectionHeader")

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.currentItemChanged.connect(self._on_current_item_changed)
        self.list_widget.itemChanged.connect(self._on_item_changed)

        self.add_button = QPushButton("Add")
        self.remove_button = QPushButton("Delete")
        self.move_up_button = QPushButton("Move Up")
        self.move_down_button = QPushButton("Move Down")
        self.enable_checkbox = QCheckBox("Enable selected entry")
        self.editor = MonospaceTextEdit()

        self.add_button.clicked.connect(self.addRequested.emit)
        self.remove_button.clicked.connect(self.removeRequested.emit)
        self.move_up_button.clicked.connect(self.moveUpRequested.emit)
        self.move_down_button.clicked.connect(self.moveDownRequested.emit)
        self.enable_checkbox.toggled.connect(self.currentEnabledChanged.emit)
        self.editor.textChanged.connect(
            lambda: self.currentTextChanged.emit(self.editor.toPlainText())
        )

        left_controls = QHBoxLayout()
        left_controls.setContentsMargins(0, 0, 0, 0)
        left_controls.setSpacing(DEFAULT_BUTTON_SPACING)
        left_controls.addWidget(self.add_button)
        left_controls.addWidget(self.remove_button)
        left_controls.addWidget(self.move_up_button)
        left_controls.addWidget(self.move_down_button)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(DEFAULT_SECTION_SPACING)
        left_layout.addLayout(left_controls)
        left_layout.addWidget(self.list_widget)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(DEFAULT_SECTION_SPACING)
        right_layout.addWidget(self.enable_checkbox)
        right_layout.addWidget(self.editor)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 520])
        splitter.setChildrenCollapsible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(DEFAULT_SECTION_SPACING)
        layout.addWidget(self.header)
        layout.addWidget(splitter)
        self._update_buttons()

    def set_items(self, items: list[EntryItemPayload]) -> None:
        """Replace the current list."""
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for payload in items:
            item = QListWidgetItem(payload.title)
            item.setData(Qt.UserRole, payload.entry_id)
            item.setToolTip(payload.subtitle)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if payload.enabled else Qt.Unchecked)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        else:
            self.editor.setPlainText("")
            self.enable_checkbox.setChecked(False)
        self._update_buttons()

    def current_entry_id(self) -> str | None:
        """Return the current selected entry id."""
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def set_current_text(self, text: str) -> None:
        """Set editor text without recursion."""
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)

    def set_current_enabled(self, enabled: bool) -> None:
        """Set enabled checkbox without recursion."""
        self.enable_checkbox.blockSignals(True)
        self.enable_checkbox.setChecked(enabled)
        self.enable_checkbox.blockSignals(False)

    def set_current_row(self, row: int) -> None:
        """Update current row."""
        if 0 <= row < self.list_widget.count():
            self.list_widget.setCurrentRow(row)

    def take_current_row(self) -> int:
        """Return current row index."""
        return self.list_widget.currentRow()

    def _on_current_item_changed(self, current, _previous) -> None:
        self._update_buttons()
        if current is None:
            self.selectionChanged.emit("")
            return
        self.selectionChanged.emit(current.data(Qt.UserRole))

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self.list_widget.currentItem() is item:
            self.enable_checkbox.blockSignals(True)
            self.enable_checkbox.setChecked(item.checkState() == Qt.Checked)
            self.enable_checkbox.blockSignals(False)
        self.changed.emit()

    def update_current_item_label(self, title: str, subtitle: str, enabled: bool) -> None:
        """Update the selected row display."""
        item = self.list_widget.currentItem()
        if item is None:
            return
        item.setText(title)
        item.setToolTip(subtitle)
        item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
        self.changed.emit()

    def _update_buttons(self) -> None:
        row = self.list_widget.currentRow()
        count = self.list_widget.count()
        has_selection = row >= 0
        self.remove_button.setEnabled(has_selection)
        self.move_up_button.setEnabled(has_selection and row > 0)
        self.move_down_button.setEnabled(has_selection and row < count - 1)
        self.enable_checkbox.setEnabled(has_selection)


class SectionFrame(QFrame):
    """Styled frame wrapper for top-level sections."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionFrame")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(
            DEFAULT_OUTER_MARGIN,
            DEFAULT_OUTER_MARGIN,
            DEFAULT_OUTER_MARGIN,
            DEFAULT_OUTER_MARGIN,
        )
        self.layout.setSpacing(DEFAULT_SECTION_SPACING)
        header = QLabel(title)
        header.setObjectName("sectionHeader")
        self.layout.addWidget(header)
