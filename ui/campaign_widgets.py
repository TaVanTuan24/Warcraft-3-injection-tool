"""Campaign-specific reusable widgets for the desktop UI."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QWidget

from models import CampaignMapEntry


class CampaignMapTable(QTableWidget):
    """Table widget for readable/unreadable campaign map entries."""

    selectionChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 5, parent)
        self._entries: list[CampaignMapEntry] = []
        self._updating = False
        self.setHorizontalHeaderLabels(
            ["Patch", "Map Name", "Archive Path", "Status", "Message"]
        )
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.itemChanged.connect(self._on_item_changed)

    def set_entries(self, entries: list[CampaignMapEntry]) -> None:
        """Replace the visible campaign map rows."""
        self._entries = list(entries)
        self._updating = True
        try:
            self.setRowCount(len(self._entries))
            for row, entry in enumerate(self._entries):
                patch_item = QTableWidgetItem()
                patch_item.setFlags(
                    Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable
                )
                patch_item.setCheckState(Qt.Checked if entry.selected else Qt.Unchecked)
                if not entry.patchable:
                    patch_item.setFlags(Qt.ItemIsSelectable)
                patch_item.setData(Qt.UserRole, entry.id)
                self.setItem(row, 0, patch_item)

                self.setItem(row, 1, QTableWidgetItem(entry.map_name))
                self.setItem(row, 2, QTableWidgetItem(entry.archive_path))
                self.setItem(row, 3, QTableWidgetItem(entry.status))
                message_item = QTableWidgetItem(entry.message)
                message_item.setToolTip(entry.message)
                self.setItem(row, 4, message_item)
        finally:
            self._updating = False
        self.resizeRowsToContents()

    def entries(self) -> list[CampaignMapEntry]:
        """Return the current campaign entries including selection state."""
        return list(self._entries)

    def selected_entries(self) -> list[CampaignMapEntry]:
        """Return currently selected-for-patching campaign entries."""
        return [entry for entry in self._entries if entry.selected]

    def select_all_patchable(self) -> None:
        """Select all readable/patchable campaign maps."""
        self._set_all_patchable(True)

    def unselect_all(self) -> None:
        """Unselect all campaign maps."""
        self._set_all_patchable(False, include_unpatchable=True)

    def _set_all_patchable(self, selected: bool, include_unpatchable: bool = False) -> None:
        self._updating = True
        try:
            for row, entry in enumerate(self._entries):
                if not entry.patchable and not include_unpatchable:
                    continue
                entry.selected = selected and entry.patchable
                item = self.item(row, 0)
                if item is not None:
                    item.setCheckState(Qt.Checked if entry.selected else Qt.Unchecked)
        finally:
            self._updating = False
        self.selectionChanged.emit()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != 0:
            return
        entry_id = item.data(Qt.UserRole)
        for index, entry in enumerate(self._entries):
            if entry.id == entry_id:
                self._entries[index] = replace(
                    entry,
                    selected=entry.patchable and item.checkState() == Qt.Checked,
                )
                break
        self.selectionChanged.emit()
