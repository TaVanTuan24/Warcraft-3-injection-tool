"""Main PySide6 window for the GUI-first trigger injector."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from models import (
    CampaignBuildSummary,
    CampaignContext,
    CampaignMapEntry,
    FunctionEntry,
    GlobalEntry,
    InputType,
    MainCallEntry,
    MapSourceContext,
    PatchResult,
    PatchRunOptions,
    PatchSelection,
    TriggerImportResult,
    ValidationResult,
    make_id,
)
from patch_config import load_patch_preset, save_patch_preset
from services.campaign_loader import dispose_campaign_source
from services.injector import effective_selection_for_map, inject_and_build_campaign, summarize_campaign_build
from services.input_detector import default_output_path, detect_input_type
from services.map_loader import dispose_map_source
from services.trigger_parser import (
    parse_functions_text,
    parse_globals_text,
    parse_main_calls_text,
    parse_trigger_file,
)
from services.validator import validate_before_inject
from ui.campaign_widgets import CampaignMapTable
from ui.dialogs import (
    TextEntryDialog,
    confirm,
    default_preset_path,
    select_open_file,
    select_open_files,
    select_save_file,
    show_error,
    show_info,
)
from ui.widgets import (
    DEFAULT_BUTTON_SPACING,
    DEFAULT_OUTER_MARGIN,
    DEFAULT_SECTION_SPACING,
    DropLineEdit,
    EntryItemPayload,
    EntryListEditor,
    LogPanel,
    MonospaceTextEdit,
    SectionFrame,
)
from utils import ToolError, normalize_for_match


class MainWindow(QMainWindow):
    """Primary desktop window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Warcraft 3 Trigger Injector")
        self.resize(1500, 1040)

        self._selection = PatchSelection.empty()
        self._trigger_files: list[str] = []
        self._listfiles: list[str] = []
        self._map_source_context: MapSourceContext | None = None
        self._campaign_context: CampaignContext | None = None
        self._input_type: InputType | None = None
        self._worker_thread: QThread | None = None
        self._worker: Worker | None = None
        self._active_group = "globals"
        self._loaded_map_text: str | None = None
        self._last_validation: ValidationResult | None = None

        self._build_ui()
        self._apply_theme()
        self._sync_raw_editors_from_selection()
        self._update_ui_state()

    def _build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QFrame.NoFrame)

        content_widget = QWidget()
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(
            DEFAULT_OUTER_MARGIN,
            DEFAULT_OUTER_MARGIN,
            DEFAULT_OUTER_MARGIN,
            DEFAULT_OUTER_MARGIN,
        )
        content_layout.setSpacing(14)

        content_layout.addWidget(self._build_file_section())
        content_layout.addWidget(self._build_campaign_section())
        content_layout.addWidget(self._build_imported_content_section())
        content_layout.addWidget(self._build_preview_section())
        content_layout.addWidget(self._build_action_section())
        content_layout.addWidget(self._build_log_section())
        content_layout.addStretch(1)

        scroll_area.setWidget(content_widget)
        root_layout.addWidget(scroll_area)

        self.setCentralWidget(central)
        self.setStatusBar(self._build_status_bar())
        self._configure_tab_order()

    def _build_file_section(self) -> QWidget:
        section = SectionFrame("Files")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(DEFAULT_SECTION_SPACING)
        grid.setVerticalSpacing(DEFAULT_BUTTON_SPACING)

        self.input_path_edit = DropLineEdit()
        self.output_path_edit = DropLineEdit()
        self.input_browse_button = QPushButton("Browse Input")
        self.output_browse_button = QPushButton("Browse Output")
        self.add_trigger_button = QPushButton("Add Trigger File")
        self.remove_trigger_button = QPushButton("Remove Trigger File")
        self.trigger_file_list = QListWidget()
        self.add_listfile_button = QPushButton("Add Listfile")
        self.remove_listfile_button = QPushButton("Remove Listfile")
        self.listfile_list = QListWidget()
        self.validation_label = QLabel()
        self.detected_script_label = QLabel("Detected script: not loaded")
        self.listfile_notice_label = QLabel(
            "If a protected map cannot expose 'war3map.j', add one or more external listfiles "
            "below. These are used only with MPQEditor during archive extraction."
        )

        self.input_browse_button.clicked.connect(self._browse_input_archive)
        self.output_browse_button.clicked.connect(self._browse_output_archive)
        self.add_trigger_button.clicked.connect(self._add_trigger_files)
        self.remove_trigger_button.clicked.connect(self._remove_trigger_file)
        self.add_listfile_button.clicked.connect(self._add_listfiles)
        self.remove_listfile_button.clicked.connect(self._remove_listfile)
        self.input_path_edit.textChanged.connect(self._on_input_path_changed)
        self.output_path_edit.textChanged.connect(self._on_paths_changed)
        self.trigger_file_list.itemSelectionChanged.connect(self._update_ui_state)
        self.listfile_list.itemSelectionChanged.connect(self._update_ui_state)

        self.input_browse_button.setToolTip("Choose the input .w3x, .w3m, or .w3n archive.")
        self.output_browse_button.setToolTip("Choose where the patched archive will be written.")
        self.add_trigger_button.setToolTip("Add one or more trigger .j files to import.")
        self.remove_trigger_button.setToolTip("Remove the selected trigger file from the list.")
        self.add_listfile_button.setToolTip("Add external MPQ listfiles for protected maps.")
        self.remove_listfile_button.setToolTip("Remove the selected external listfile.")
        self.trigger_file_list.setMinimumHeight(72)
        self.trigger_file_list.setMaximumHeight(112)
        self.listfile_list.setMinimumHeight(64)
        self.listfile_list.setMaximumHeight(96)
        self.validation_label.setWordWrap(True)
        self.detected_script_label.setWordWrap(True)
        self.listfile_notice_label.setWordWrap(True)
        self.detected_script_label.setObjectName("supportNote")
        self.listfile_notice_label.setObjectName("supportNote")

        grid.addWidget(QLabel("Input archive"), 0, 0)
        grid.addWidget(self.input_path_edit, 0, 1)
        grid.addWidget(self.input_browse_button, 0, 2)
        grid.addWidget(QLabel("Output archive"), 1, 0)
        grid.addWidget(self.output_path_edit, 1, 1)
        grid.addWidget(self.output_browse_button, 1, 2)
        grid.addWidget(QLabel("Trigger files"), 2, 0, alignment=Qt.AlignTop)

        trigger_panel = QWidget()
        trigger_panel_layout = QVBoxLayout(trigger_panel)
        trigger_panel_layout.setContentsMargins(0, 0, 0, 0)
        trigger_panel_layout.setSpacing(DEFAULT_BUTTON_SPACING)
        trigger_panel_layout.addWidget(self.trigger_file_list)

        trigger_buttons_row = QHBoxLayout()
        trigger_buttons_row.setContentsMargins(0, 0, 0, 0)
        trigger_buttons_row.setSpacing(DEFAULT_BUTTON_SPACING)
        trigger_buttons_row.addWidget(self.add_trigger_button)
        trigger_buttons_row.addWidget(self.remove_trigger_button)
        trigger_buttons_row.addStretch(1)
        trigger_panel_layout.addLayout(trigger_buttons_row)
        grid.addWidget(trigger_panel, 2, 1, 1, 2)

        grid.addWidget(QLabel("Listfiles"), 3, 0, alignment=Qt.AlignTop)

        listfile_panel = QWidget()
        listfile_panel_layout = QVBoxLayout(listfile_panel)
        listfile_panel_layout.setContentsMargins(0, 0, 0, 0)
        listfile_panel_layout.setSpacing(DEFAULT_BUTTON_SPACING)
        listfile_panel_layout.addWidget(self.listfile_list)

        listfile_buttons_row = QHBoxLayout()
        listfile_buttons_row.setContentsMargins(0, 0, 0, 0)
        listfile_buttons_row.setSpacing(DEFAULT_BUTTON_SPACING)
        listfile_buttons_row.addWidget(self.add_listfile_button)
        listfile_buttons_row.addWidget(self.remove_listfile_button)
        listfile_buttons_row.addStretch(1)
        listfile_panel_layout.addLayout(listfile_buttons_row)
        grid.addWidget(listfile_panel, 3, 1, 1, 2)

        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Archive note"), 4, 0, alignment=Qt.AlignTop)
        grid.addWidget(self.listfile_notice_label, 4, 1, 1, 2)
        grid.addWidget(QLabel("State"), 5, 0)
        grid.addWidget(self.validation_label, 5, 1, 1, 2)
        grid.addWidget(QLabel("Detected script"), 6, 0, alignment=Qt.AlignTop)
        grid.addWidget(self.detected_script_label, 6, 1, 1, 2)
        section.layout.addLayout(grid)
        return section

    def _build_campaign_section(self) -> QWidget:
        section = SectionFrame("Campaign Maps")
        section.setMinimumHeight(300)
        section.setVisible(False)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(DEFAULT_BUTTON_SPACING)

        self.select_all_maps_button = QPushButton("Select All Maps")
        self.unselect_all_maps_button = QPushButton("Unselect All Maps")
        self.refresh_campaign_button = QPushButton("Refresh Campaign Scan")
        self.campaign_failure_combo = QComboBox()
        self.campaign_failure_combo.addItems(
            [
                "Skip failed maps and continue",
                "Stop on first error",
            ]
        )
        self.campaign_status_label = QLabel("No campaign loaded.")
        self.campaign_status_label.setWordWrap(True)

        self.select_all_maps_button.clicked.connect(self._select_all_campaign_maps)
        self.unselect_all_maps_button.clicked.connect(self._unselect_all_campaign_maps)
        self.refresh_campaign_button.clicked.connect(self._refresh_campaign_scan)
        self.campaign_failure_combo.currentIndexChanged.connect(self._update_ui_state)

        controls_row.addWidget(self.select_all_maps_button)
        controls_row.addWidget(self.unselect_all_maps_button)
        controls_row.addWidget(self.refresh_campaign_button)
        controls_row.addStretch(1)
        controls_row.addWidget(QLabel("Failure handling"))
        controls_row.addWidget(self.campaign_failure_combo)

        self.campaign_map_table = CampaignMapTable()
        self.campaign_map_table.setMinimumHeight(220)
        self.campaign_map_table.selectionChanged.connect(self._update_ui_state)

        section.layout.addLayout(controls_row)
        section.layout.addWidget(self.campaign_map_table)
        section.layout.addWidget(self.campaign_status_label)
        self.campaign_section = section
        return section

    def _build_imported_content_section(self) -> QWidget:
        section = SectionFrame("Imported Content")
        section.setMinimumHeight(420)
        section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Structured Mode", "Raw Mode"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(DEFAULT_BUTTON_SPACING)
        mode_row.addWidget(QLabel("Editing mode"))
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch(1)
        section.layout.addLayout(mode_row)

        self.content_tabs = QTabWidget()
        self.globals_tab = self._build_group_tab("globals", "Globals")
        self.functions_tab = self._build_group_tab("functions", "Functions")
        self.calls_tab = self._build_group_tab("calls", "Main Calls")
        self.content_tabs.addTab(self.globals_tab, "Globals")
        self.content_tabs.addTab(self.functions_tab, "Functions")
        self.content_tabs.addTab(self.calls_tab, "Main Calls")
        self.content_tabs.currentChanged.connect(self._on_tab_changed)
        self.content_tabs.setMinimumHeight(340)

        section.layout.addWidget(self.content_tabs)
        return section

    def _build_group_tab(self, group_name: str, title: str) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(DEFAULT_SECTION_SPACING)

        editor = EntryListEditor(title)
        raw_editor = MonospaceTextEdit()
        editor.selectionChanged.connect(lambda entry_id, group=group_name: self._on_entry_selected(group, entry_id))
        editor.currentTextChanged.connect(lambda text, group=group_name: self._on_entry_text_changed(group, text))
        editor.currentEnabledChanged.connect(lambda enabled, group=group_name: self._on_entry_enabled_changed(group, enabled))
        editor.addRequested.connect(lambda group=group_name: self._add_entry(group))
        editor.removeRequested.connect(lambda group=group_name: self._remove_entry(group))
        editor.moveUpRequested.connect(lambda group=group_name: self._move_entry(group, -1))
        editor.moveDownRequested.connect(lambda group=group_name: self._move_entry(group, 1))
        raw_editor.textChanged.connect(lambda group=group_name: self._on_raw_group_changed(group))

        setattr(self, f"{group_name}_editor_widget", editor)
        setattr(self, f"{group_name}_raw_editor", raw_editor)

        layout.addWidget(editor)
        layout.addWidget(raw_editor)
        return container

    def _build_preview_section(self) -> QWidget:
        section = SectionFrame("Final Patch Preview")
        section.setMinimumHeight(280)
        section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.preview_tabs = QTabWidget()
        self.preview_globals = MonospaceTextEdit()
        self.preview_functions = MonospaceTextEdit()
        self.preview_calls = MonospaceTextEdit()
        self.preview_merged = MonospaceTextEdit()
        for widget in (
            self.preview_globals,
            self.preview_functions,
            self.preview_calls,
            self.preview_merged,
        ):
            widget.setReadOnly(True)
            widget.setMinimumHeight(120)
        self.preview_tabs.addTab(self.preview_globals, "Globals")
        self.preview_tabs.addTab(self.preview_functions, "Before Main")
        self.preview_tabs.addTab(self.preview_calls, "Inside Main")
        self.preview_tabs.addTab(self.preview_merged, "Merged Preview")
        self.preview_tabs.setMinimumHeight(230)
        section.layout.addWidget(self.preview_tabs)
        return section

    def _build_action_section(self) -> QWidget:
        section = SectionFrame("Actions")
        section.setMinimumHeight(130)
        self.load_input_button = QPushButton("Load Input Source")
        self.scan_trigger_button = QPushButton("Scan Trigger Files")
        self.validate_button = QPushButton("Validate")
        self.inject_button = QPushButton("Inject & Build")
        self.save_preset_button = QPushButton("Save Patch Preset")
        self.load_preset_button = QPushButton("Load Patch Preset")
        self.reset_button = QPushButton("Reset")
        self.overwrite_checkbox = QCheckBox("Overwrite output")
        self.keep_temp_checkbox = QCheckBox("Keep temp workspace")

        self.load_input_button.clicked.connect(self._load_input_source)
        self.scan_trigger_button.clicked.connect(self._scan_trigger_files)
        self.validate_button.clicked.connect(self._validate_current)
        self.inject_button.clicked.connect(self._inject_and_build)
        self.save_preset_button.clicked.connect(self._save_preset)
        self.load_preset_button.clicked.connect(self._load_preset)
        self.reset_button.clicked.connect(self._reset_all)

        action_buttons = (
            self.load_input_button,
            self.scan_trigger_button,
            self.validate_button,
            self.inject_button,
        )
        library_buttons = (
            self.save_preset_button,
            self.load_preset_button,
            self.reset_button,
        )

        button_tooltips = {
            self.load_input_button: "Load the selected map or campaign into the workspace.",
            self.scan_trigger_button: "Parse all selected trigger files and import patchable content.",
            self.validate_button: "Run pre-build validation using the current files and selections.",
            self.inject_button: "Inject the selected content and build the output archive.",
            self.save_preset_button: "Save the current selected patch entries to a preset file.",
            self.load_preset_button: "Load patch entries from a previously saved preset file.",
            self.reset_button: "Clear the current UI state and selections.",
        }
        for button, tooltip in button_tooltips.items():
            button.setToolTip(tooltip)
            button.setMinimumHeight(34)
            button.setMinimumWidth(136)
            button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

        self.inject_button.setMinimumWidth(152)
        self.save_preset_button.setMinimumWidth(152)
        self.load_preset_button.setMinimumWidth(152)

        primary_row = QHBoxLayout()
        primary_row.setContentsMargins(0, 0, 0, 0)
        primary_row.setSpacing(10)
        for button in action_buttons:
            primary_row.addWidget(button)
        primary_row.addStretch(1)

        secondary_row = QHBoxLayout()
        secondary_row.setContentsMargins(0, 0, 0, 0)
        secondary_row.setSpacing(10)
        for button in library_buttons:
            secondary_row.addWidget(button)
        secondary_row.addSpacing(12)
        secondary_row.addWidget(self.overwrite_checkbox)
        secondary_row.addWidget(self.keep_temp_checkbox)
        secondary_row.addStretch(1)

        section.layout.addLayout(primary_row)
        section.layout.addLayout(secondary_row)
        return section

    def _build_log_section(self) -> QWidget:
        section = SectionFrame("Logs")
        self.log_panel = LogPanel()
        self.log_panel.setPlaceholderText("Run an action to see log output here.")
        self.log_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        section.layout.addWidget(self.log_panel)
        section.setMinimumHeight(250)
        return section

    def _build_status_bar(self) -> QStatusBar:
        status = QStatusBar()
        self.status_file_label = QLabel("File state: blocked")
        self.status_action_label = QLabel("Last action: idle")
        self.status_run_label = QLabel("State: Ready")
        status.addPermanentWidget(self.status_file_label)
        status.addPermanentWidget(self.status_action_label)
        status.addPermanentWidget(self.status_run_label)
        return status

    def _configure_tab_order(self) -> None:
        tab_chain = [
            self.input_path_edit,
            self.input_browse_button,
            self.output_path_edit,
            self.output_browse_button,
            self.trigger_file_list,
            self.add_trigger_button,
            self.remove_trigger_button,
            self.listfile_list,
            self.add_listfile_button,
            self.remove_listfile_button,
            self.select_all_maps_button,
            self.unselect_all_maps_button,
            self.refresh_campaign_button,
            self.mode_combo,
            self.content_tabs,
            self.preview_tabs,
            self.load_input_button,
            self.scan_trigger_button,
            self.validate_button,
            self.inject_button,
            self.save_preset_button,
            self.load_preset_button,
            self.reset_button,
            self.overwrite_checkbox,
            self.keep_temp_checkbox,
            self.log_panel,
        ]
        for first, second in zip(tab_chain, tab_chain[1:]):
            self.setTabOrder(first, second)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background: #1f242b; color: #d8dee9; font-size: 13px; }
            QScrollArea { background: #1f242b; border: none; }
            QFrame#sectionFrame { background: #262d37; border: 1px solid #3d4654; border-radius: 8px; }
            QLabel#sectionHeader { font-size: 14px; font-weight: 600; color: white; }
            QLabel#supportNote { color: #f2c94c; }
            QPushButton, QComboBox, QPlainTextEdit, QListWidget, QCheckBox, QTableWidget {
                background: #13181e; color: #e6edf3; border: 1px solid #3d4654; border-radius: 6px; padding: 6px;
            }
            QPushButton:hover { border-color: #7aa2f7; }
            QPushButton:disabled { color: #7d8590; border-color: #30363d; }
            QTabWidget::pane { border: 1px solid #3d4654; background: #13181e; border-radius: 6px; }
            QTabBar::tab { background: #262d37; color: #c9d1d9; padding: 8px 12px; margin-right: 3px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #13181e; color: white; }
            QHeaderView::section { background: #262d37; color: #c9d1d9; padding: 6px; border: 1px solid #3d4654; }
            QStatusBar { background: #13181e; color: #c9d1d9; }
            """
        )

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._release_loaded_input_source()
        super().closeEvent(event)

    def _browse_input_archive(self) -> None:
        path = select_open_file(
            self,
            "Select Input Archive",
            "Warcraft 3 Archives (*.w3x *.w3m *.w3n)",
        )
        if not path:
            return
        self.input_path_edit.setText(path)
        if not self.output_path_edit.text():
            try:
                self.output_path_edit.setText(str(default_output_path(Path(path))))
            except Exception:
                pass

    def _browse_output_archive(self) -> None:
        suffix = self._input_type.suffix if self._input_type is not None else ".w3x"
        file_filter = f"Warcraft 3 Output (*{suffix})"
        path = select_save_file(
            self,
            "Select Output Archive",
            file_filter,
            self.output_path_edit.text() or self.input_path_edit.text(),
        )
        if path:
            self.output_path_edit.setText(path)

    def _add_trigger_files(self) -> None:
        paths = select_open_files(self, "Select Trigger Files", "JASS Trigger (*.j)")
        if not paths:
            return
        added = 0
        for path in paths:
            if path not in self._trigger_files:
                self._trigger_files.append(path)
                self.trigger_file_list.addItem(path)
                added += 1
        self._append_log("INFO", f"Added {added} trigger file(s).")
        self._update_ui_state()

    def _add_listfiles(self) -> None:
        paths = select_open_files(
            self,
            "Select External Listfiles",
            "Listfiles (*.txt *.lst *.listfile);;All Files (*)",
        )
        if not paths:
            return
        added = 0
        for path in paths:
            if path not in self._listfiles:
                self._listfiles.append(path)
                self.listfile_list.addItem(path)
                added += 1
        if added:
            self._append_log("INFO", f"Added {added} external listfile(s).")
            self._on_archive_options_changed()
            return
        self._update_ui_state()

    def _remove_trigger_file(self) -> None:
        row = self.trigger_file_list.currentRow()
        if row < 0:
            return
        removed = self._trigger_files.pop(row)
        self.trigger_file_list.takeItem(row)
        self._append_log("INFO", f"Removed trigger file: {removed}")
        self._update_ui_state()

    def _remove_listfile(self) -> None:
        row = self.listfile_list.currentRow()
        if row < 0:
            return
        removed = self._listfiles.pop(row)
        self.listfile_list.takeItem(row)
        self._append_log("INFO", f"Removed external listfile: {removed}")
        self._on_archive_options_changed()

    def _select_all_campaign_maps(self) -> None:
        self.campaign_map_table.select_all_patchable()
        self._sync_campaign_entries_from_table()
        self._update_ui_state()

    def _unselect_all_campaign_maps(self) -> None:
        self.campaign_map_table.unselect_all()
        self._sync_campaign_entries_from_table()
        self._update_ui_state()

    def _refresh_campaign_scan(self) -> None:
        input_path = self._input_path()
        if input_path is None or self._input_type != InputType.CAMPAIGN_W3N:
            show_error(self, "Refresh Campaign Scan", "Select a valid input .w3n campaign first.")
            return
        if (
            self._campaign_context is None
            or self._campaign_context.external_listfiles != self._external_listfiles()
        ):
            self._run_worker(
                "load-input",
                input_path=input_path,
                external_listfiles=self._external_listfiles(),
            )
            return
        self._run_worker(
            "scan-campaign",
            input_path=input_path,
            campaign_context=self._campaign_context,
        )

    def _on_input_path_changed(self) -> None:
        previous_type = self._input_type
        self._release_loaded_input_source()
        self._input_type = None
        input_path = self._input_path()
        if input_path is not None and input_path.suffix:
            try:
                self._input_type = detect_input_type(input_path)
            except Exception:
                self._input_type = None

        if input_path is not None and self._input_type is not None:
            try:
                default_output = str(default_output_path(input_path))
            except Exception:
                default_output = ""
            if not self.output_path_edit.text() or (
                previous_type is not None
                and self.output_path_edit.text().endswith(previous_type.suffix)
                and "_patched" in self.output_path_edit.text()
            ):
                self.output_path_edit.setText(default_output)
                return
        self._on_paths_changed()

    def _on_paths_changed(self) -> None:
        self._last_validation = None
        self._sync_campaign_entries_from_table()
        self._update_ui_state()
        self._refresh_preview()

    def _on_archive_options_changed(self) -> None:
        self._release_loaded_input_source()
        self._last_validation = None
        self._update_ui_state()
        self._refresh_preview()

    def _on_mode_changed(self, index: int) -> None:
        structured_mode = index == 0
        for group in ("globals", "functions", "calls"):
            getattr(self, f"{group}_editor_widget").setVisible(structured_mode)
            getattr(self, f"{group}_raw_editor").setVisible(not structured_mode)
        if not structured_mode:
            self._sync_raw_editors_from_selection()
        else:
            try:
                self._sync_selection_from_raw_editors()
            except ToolError as exc:
                show_error(self, "Raw Mode Error", str(exc))
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(1)
                self.mode_combo.blockSignals(False)
                return
            self._refresh_structured_views()
        self._update_ui_state()
        self._refresh_preview()

    def _on_tab_changed(self, index: int) -> None:
        self._active_group = ("globals", "functions", "calls")[index]

    def _entries_for_group(self, group: str):
        if group == "globals":
            return self._selection.globals_entries
        if group == "functions":
            return self._selection.function_entries
        return self._selection.main_call_entries

    def _make_payload(self, entry) -> EntryItemPayload:
        title = getattr(entry, "name", "") or entry.text.strip().splitlines()[0]
        subtitle = f"{entry.source_file} | {'enabled' if entry.enabled else 'disabled'}"
        return EntryItemPayload(
            entry_id=entry.id,
            title=title[:80],
            subtitle=subtitle,
            enabled=entry.enabled,
        )

    def _refresh_structured_views(self) -> None:
        for group in ("globals", "functions", "calls"):
            widget: EntryListEditor = getattr(self, f"{group}_editor_widget")
            items = [self._make_payload(entry) for entry in self._entries_for_group(group)]
            current_id = widget.current_entry_id()
            widget.set_items(items)
            if current_id:
                for index, entry in enumerate(self._entries_for_group(group)):
                    if entry.id == current_id:
                        widget.set_current_row(index)
                        break

    def _sync_raw_editors_from_selection(self) -> None:
        self.globals_raw_editor.blockSignals(True)
        self.functions_raw_editor.blockSignals(True)
        self.calls_raw_editor.blockSignals(True)
        self.globals_raw_editor.setPlainText("\n".join(entry.text for entry in self._selection.globals_entries))
        self.functions_raw_editor.setPlainText("\n\n".join(entry.text for entry in self._selection.function_entries))
        self.calls_raw_editor.setPlainText("\n".join(entry.text for entry in self._selection.main_call_entries))
        self.globals_raw_editor.blockSignals(False)
        self.functions_raw_editor.blockSignals(False)
        self.calls_raw_editor.blockSignals(False)
        self._refresh_structured_views()

    def _sync_selection_from_raw_editors(self) -> None:
        self._selection.globals_entries = parse_globals_text(
            self.globals_raw_editor.toPlainText(), "raw-editor"
        )
        self._selection.function_entries = parse_functions_text(
            self.functions_raw_editor.toPlainText(), "raw-editor"
        )
        self._selection.main_call_entries = parse_main_calls_text(
            self.calls_raw_editor.toPlainText(), "raw-editor"
        )

    def _sync_campaign_entries_from_table(self) -> None:
        if self._campaign_context is not None:
            self._campaign_context.map_entries = self.campaign_map_table.entries()

    def _on_raw_group_changed(self, _group: str) -> None:
        if self.mode_combo.currentIndex() != 1:
            return
        try:
            self._sync_selection_from_raw_editors()
            self._refresh_structured_views()
            self._refresh_preview()
            self._update_ui_state()
        except ToolError:
            pass

    def _find_entry(self, group: str, entry_id: str):
        for entry in self._entries_for_group(group):
            if entry.id == entry_id:
                return entry
        return None

    def _on_entry_selected(self, group: str, entry_id: str) -> None:
        widget: EntryListEditor = getattr(self, f"{group}_editor_widget")
        entry = self._find_entry(group, entry_id)
        if entry is None:
            widget.set_current_text("")
            widget.set_current_enabled(False)
            return
        widget.set_current_text(entry.text)
        widget.set_current_enabled(entry.enabled)

    def _on_entry_text_changed(self, group: str, text: str) -> None:
        widget: EntryListEditor = getattr(self, f"{group}_editor_widget")
        entry = self._find_entry(group, widget.current_entry_id() or "")
        if entry is None:
            return
        entry.text = text.strip("\n")
        if group == "functions":
            lines = entry.text.splitlines()
            if lines:
                entry.signature = normalize_for_match(lines[0])
                entry.name = _guess_function_name(lines[0])
        elif group == "globals":
            entry.name = _guess_global_name(entry.text)
        else:
            entry.name = _guess_call_name(entry.text)
        widget.update_current_item_label(
            title=getattr(entry, "name", "") or entry.text.strip().splitlines()[0],
            subtitle=f"{entry.source_file} | {'enabled' if entry.enabled else 'disabled'}",
            enabled=entry.enabled,
        )
        self._sync_raw_editors_from_selection()
        self._refresh_preview()

    def _on_entry_enabled_changed(self, group: str, enabled: bool) -> None:
        widget: EntryListEditor = getattr(self, f"{group}_editor_widget")
        entry = self._find_entry(group, widget.current_entry_id() or "")
        if entry is None:
            return
        entry.enabled = enabled
        widget.update_current_item_label(
            title=getattr(entry, "name", "") or entry.text.strip().splitlines()[0],
            subtitle=f"{entry.source_file} | {'enabled' if entry.enabled else 'disabled'}",
            enabled=entry.enabled,
        )
        self._sync_raw_editors_from_selection()
        self._refresh_preview()
        self._update_ui_state()

    def _add_entry(self, group: str) -> None:
        seed_map = {
            "globals": "integer udg_NewValue = 0",
            "functions": "function NewFunction takes nothing returns nothing\nendfunction",
            "calls": "call InitTrig_NewFunction()",
        }
        dialog = TextEntryDialog(self, f"Add {group.title()}", seed_map[group])
        if dialog.exec() != dialog.Accepted:
            return
        text = dialog.value().strip()
        if not text:
            return
        if group == "globals":
            entry = GlobalEntry(make_id("global"), True, "manual", text, _guess_global_name(text))
            self._selection.globals_entries.append(entry)
        elif group == "functions":
            entry = FunctionEntry(
                make_id("function"),
                True,
                "manual",
                text,
                normalize_for_match(text.splitlines()[0]),
                _guess_function_name(text.splitlines()[0]),
            )
            self._selection.function_entries.append(entry)
        else:
            entry = MainCallEntry(make_id("call"), True, "manual", text, _guess_call_name(text))
            self._selection.main_call_entries.append(entry)
        self._refresh_structured_views()
        self._sync_raw_editors_from_selection()
        self._refresh_preview()
        self._update_ui_state()

    def _remove_entry(self, group: str) -> None:
        widget: EntryListEditor = getattr(self, f"{group}_editor_widget")
        current_id = widget.current_entry_id()
        if not current_id:
            return
        entries = self._entries_for_group(group)
        entries[:] = [entry for entry in entries if entry.id != current_id]
        self._refresh_structured_views()
        self._sync_raw_editors_from_selection()
        self._refresh_preview()
        self._update_ui_state()

    def _move_entry(self, group: str, offset: int) -> None:
        widget: EntryListEditor = getattr(self, f"{group}_editor_widget")
        row = widget.take_current_row()
        entries = self._entries_for_group(group)
        target = row + offset
        if row < 0 or target < 0 or target >= len(entries):
            return
        entries[row], entries[target] = entries[target], entries[row]
        self._refresh_structured_views()
        widget.set_current_row(target)
        self._sync_raw_editors_from_selection()
        self._refresh_preview()

    def _load_input_source(self) -> None:
        input_path = self._input_path()
        if input_path is None:
            show_error(self, "Load Input Source", "Select a valid input archive first.")
            return
        self._run_worker(
            "load-input",
            input_path=input_path,
            external_listfiles=self._external_listfiles(),
        )

    def _scan_trigger_files(self) -> None:
        if not self._trigger_files:
            show_error(self, "Scan Trigger Files", "Add at least one trigger file first.")
            return
        self._run_worker("scan-triggers", trigger_files=[Path(path) for path in self._trigger_files])

    def _validate_current(self) -> None:
        if self.mode_combo.currentIndex() == 1:
            try:
                self._sync_selection_from_raw_editors()
            except ToolError as exc:
                show_error(self, "Validation Failed", str(exc))
                return
        validation = validate_before_inject(
            input_map=self._input_path(),
            output_map=self._output_path(),
            selected_patch=self._selection,
            overwrite=self.overwrite_checkbox.isChecked(),
            map_source=self._map_source_context,
            source_text=self._loaded_map_text,
            campaign_maps=self.campaign_map_table.entries() if self._input_type == InputType.CAMPAIGN_W3N else None,
            stop_on_first_error=self._stop_on_first_error(),
        )
        self._last_validation = validation
        if validation.is_valid:
            message = "Validation passed."
            if validation.warnings:
                message += " " + " ".join(validation.warnings)
            self._append_log("SUCCESS", message)
            show_info(self, "Validation", message)
            self.status_run_label.setText("State: Validated")
        else:
            message = "\n".join(validation.issues)
            self._append_log("ERROR", message)
            show_error(self, "Validation Failed", message)
            self.status_run_label.setText("State: Failed")
        self._refresh_preview()
        self._update_ui_state()

    def _inject_and_build(self) -> None:
        if self.mode_combo.currentIndex() == 1:
            try:
                self._sync_selection_from_raw_editors()
            except ToolError as exc:
                show_error(self, "Inject & Build", str(exc))
                return

        input_path = self._input_path()
        output_path = self._output_path()
        if input_path is None or output_path is None:
            show_error(self, "Inject & Build", "Select valid input and output archive paths first.")
            return

        overwrite = self.overwrite_checkbox.isChecked()
        if output_path.exists() and not overwrite:
            overwrite = confirm(
                self,
                "Overwrite Output",
                "The selected output file already exists. Overwrite it?",
            )
            if not overwrite:
                return

        kwargs = {
            "input_path": input_path,
            "input_type": self._input_type,
            "output_path": output_path,
            "selection": self._selection,
            "external_listfiles": self._external_listfiles(),
            "options": PatchRunOptions(
                overwrite=overwrite,
                keep_temp=self.keep_temp_checkbox.isChecked(),
                stop_on_first_error=self._stop_on_first_error(),
            ),
        }
        if self._input_type == InputType.CAMPAIGN_W3N:
            if self._campaign_context is None:
                show_error(self, "Inject & Build", "Load and scan a campaign before building.")
                return
            self._sync_campaign_entries_from_table()
            kwargs["campaign_context"] = self._campaign_context
            kwargs["campaign_maps"] = self.campaign_map_table.selected_entries()

        self._run_worker("inject-build", **kwargs)

    def _save_preset(self) -> None:
        if self.mode_combo.currentIndex() == 1:
            try:
                self._sync_selection_from_raw_editors()
            except ToolError as exc:
                show_error(self, "Save Preset Failed", str(exc))
                return
        path = select_save_file(
            self,
            "Save Patch Preset",
            "JSON Files (*.json)",
            default_preset_path(),
        )
        if not path:
            return
        save_patch_preset(Path(path), self._selection)
        self._append_log("SUCCESS", f"Saved preset: {path}")

    def _load_preset(self) -> None:
        path = select_open_file(
            self,
            "Load Patch Preset",
            "JSON Files (*.json)",
            default_preset_path(),
        )
        if not path:
            return
        self._selection = load_patch_preset(Path(path))
        self._sync_raw_editors_from_selection()
        self._refresh_structured_views()
        self._refresh_preview()
        self._append_log("SUCCESS", f"Loaded preset: {path}")
        self._update_ui_state()

    def _reset_all(self) -> None:
        if self._worker_thread and self._worker_thread.isRunning():
            return
        self._release_loaded_input_source()
        self._selection = PatchSelection.empty()
        self._trigger_files.clear()
        self._listfiles.clear()
        self.trigger_file_list.clear()
        self.listfile_list.clear()
        self.input_path_edit.setText("")
        self.output_path_edit.setText("")
        self.overwrite_checkbox.setChecked(False)
        self.keep_temp_checkbox.setChecked(False)
        self.campaign_map_table.set_entries([])
        self.campaign_status_label.setText("No campaign loaded.")
        self.log_panel.clear()
        self._input_type = None
        self._sync_raw_editors_from_selection()
        self._refresh_structured_views()
        self._refresh_preview()
        self.status_run_label.setText("State: Ready")
        self.status_action_label.setText("Last action: reset")
        self._update_ui_state()

    def _input_path(self) -> Path | None:
        text = self.input_path_edit.text()
        return Path(text).expanduser().resolve() if text else None

    def _output_path(self) -> Path | None:
        text = self.output_path_edit.text()
        return Path(text).expanduser().resolve() if text else None

    def _external_listfiles(self) -> tuple[Path, ...]:
        return tuple(Path(path).expanduser().resolve() for path in self._listfiles)

    def _stop_on_first_error(self) -> bool:
        return self.campaign_failure_combo.currentIndex() == 1

    def _release_loaded_input_source(self) -> None:
        if self._map_source_context is not None:
            dispose_map_source(
                self._map_source_context,
                keep=self.keep_temp_checkbox.isChecked(),
                log_callback=self._append_log,
            )
            self._map_source_context = None
            self._loaded_map_text = None
        if self._campaign_context is not None:
            dispose_campaign_source(
                self._campaign_context,
                keep=self.keep_temp_checkbox.isChecked(),
                log_callback=self._append_log,
            )
            self._campaign_context = None
            self.campaign_map_table.set_entries([])
            self.campaign_status_label.setText("No campaign loaded.")
        self._update_detected_script_label()

    def _run_worker(self, mode: str, **kwargs) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            return
        self._worker_thread = QThread(self)
        self._worker = Worker(mode=mode, **kwargs)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.logEmitted.connect(self._append_log)
        self._worker.progressChanged.connect(self._on_progress)
        self._worker.scanCompleted.connect(self._on_scan_completed)
        self._worker.inputLoaded.connect(self._on_input_loaded)
        self._worker.injectBuildCompleted.connect(self._on_inject_build_completed)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._cleanup_worker)
        self.status_action_label.setText(f"Last action: {mode}")
        self.status_run_label.setText("State: Running")
        self._update_ui_state()
        self._worker_thread.start()

    def _on_progress(self, step: str) -> None:
        self.status_run_label.setText(f"State: {step}")

    def _on_scan_completed(self, imported_results: list[TriggerImportResult]) -> None:
        self._selection = PatchSelection.empty()
        for imported in imported_results:
            self._selection.extend_from_import(imported)
        self._sync_raw_editors_from_selection()
        self._refresh_structured_views()
        self._refresh_preview()
        self._append_log("SUCCESS", "Trigger scan complete.")
        self.status_run_label.setText("State: Scanned")
        self._update_ui_state()

    def _on_input_loaded(self, payload: dict) -> None:
        self._input_type = payload["input_type"]
        if self._input_type is not None and self._input_type.is_map:
            self._release_loaded_input_source()
            self._map_source_context = payload["map_context"]
            self._loaded_map_text = self._map_source_context.source_text
            self._append_log(
                "SUCCESS",
                "Map source loaded successfully. "
                f"Detected script: {self._map_source_context.script_relative_path.as_posix()}",
            )
        else:
            self._release_loaded_input_source()
            self._campaign_context = payload["campaign_context"]
            self.campaign_map_table.set_entries(payload["campaign_maps"])
            self.campaign_status_label.setText(self._format_campaign_status())
            self._append_log(
                "SUCCESS",
                f"Campaign scan complete. Found {len(payload['campaign_maps'])} embedded map archive(s).",
            )
        self._update_detected_script_label()
        self._refresh_preview()
        self.status_run_label.setText("State: Ready")
        self._update_ui_state()

    def _on_inject_build_completed(self, result) -> None:
        if isinstance(result, CampaignBuildSummary):
            summary_text = summarize_campaign_build(result)
            self._append_log("SUCCESS", summary_text)
            show_info(self, "Campaign Build Complete", summary_text)
            self.status_run_label.setText("State: Built campaign")
        else:
            self._append_log(
                "SUCCESS",
                (
                    f"Inject & Build completed: {result.output_path} "
                    f"(globals={result.added_globals}, functions={result.added_functions}, "
                    f"main calls={result.added_init_calls})"
                ),
            )
            show_info(
                self,
                "Build Complete",
                (
                    f"Patched archive created:\n{result.output_path}\n\n"
                    f"Added globals: {result.added_globals}\n"
                    f"Added functions: {result.added_functions}\n"
                    f"Added main calls: {result.added_init_calls}"
                ),
            )
            self.status_run_label.setText("State: Built")
        self._update_ui_state()

    def _on_worker_failed(self, message: str) -> None:
        self._append_log("ERROR", message)
        show_error(self, "Operation Failed", message)
        self.status_run_label.setText("State: Failed")
        self._update_ui_state()

    def _cleanup_worker(self) -> None:
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None
        self._worker_thread = None
        self._update_ui_state()

    def _refresh_preview(self) -> None:
        preview_selection = self._selection
        if self._loaded_map_text is not None:
            try:
                preview_selection = effective_selection_for_map(
                    self._loaded_map_text,
                    self._selection,
                )
            except Exception:
                preview_selection = self._selection

        globals_text = "\n".join(entry.text for entry in preview_selection.enabled_globals())
        functions_text = "\n\n".join(entry.text for entry in preview_selection.enabled_functions())
        calls_text = "\n".join(entry.text for entry in preview_selection.enabled_main_calls())

        self.preview_globals.setPlainText(globals_text or "// No globals to add")
        self.preview_functions.setPlainText(functions_text or "// No functions to add")
        self.preview_calls.setPlainText(calls_text or "// No main calls to add")

        merged_lines = []
        if globals_text:
            merged_lines.append("[globals]")
            merged_lines.extend(f"+ {line}" for line in globals_text.splitlines())
        if functions_text:
            merged_lines.append("")
            merged_lines.append("[before function main]")
            merged_lines.extend(f"+ {line}" for line in functions_text.splitlines())
        if calls_text:
            merged_lines.append("")
            merged_lines.append("[inside function main]")
            merged_lines.extend(f"+ {line}" for line in calls_text.splitlines())
        self.preview_merged.setPlainText(
            "\n".join(merged_lines).strip() or "// Nothing will be inserted"
        )

    def _update_detected_script_label(self) -> None:
        if self._map_source_context is not None:
            self.detected_script_label.setText(
                f"Detected script: {self._map_source_context.script_relative_path.as_posix()}"
            )
            return
        if self._campaign_context is not None:
            self.detected_script_label.setText("Detected script: campaign mode")
            return
        self.detected_script_label.setText("Detected script: not loaded")

    def _format_campaign_status(self) -> str:
        entries = self.campaign_map_table.entries()
        total = len(entries)
        selected = len([entry for entry in entries if entry.selected])
        patchable = len([entry for entry in entries if entry.patchable])
        failed = len([entry for entry in entries if not entry.patchable])
        return (
            f"Campaign maps: total={total} | selected={selected} | patchable={patchable} | "
            f"unpatchable={failed}"
        )

    def _update_ui_state(self) -> None:
        input_valid = bool(self._input_path() and self._input_path().exists() and self._input_type is not None)
        output_valid = bool(
            self._output_path()
            and self._input_type is not None
            and self._output_path().suffix.lower() == self._input_type.suffix
        )
        has_trigger_files = bool(self._trigger_files)
        listfile_count = len(self._listfiles)
        has_enabled_entries = bool(
            self._selection.enabled_globals()
            or self._selection.enabled_functions()
            or self._selection.enabled_main_calls()
        )
        worker_running = self._worker_thread is not None and self._worker_thread.isRunning()
        campaign_mode = self._input_type == InputType.CAMPAIGN_W3N
        selected_campaign_maps = len(self.campaign_map_table.selected_entries())

        self.campaign_section.setVisible(campaign_mode)
        if campaign_mode:
            self.campaign_status_label.setText(self._format_campaign_status())

        input_type_label = self._input_type.name if self._input_type is not None else "unknown"
        self.validation_label.setText(
            " | ".join(
                [
                    f"Input: {'ready' if input_valid else 'missing'}",
                    f"Type: {input_type_label}",
                    f"Output: {'ready' if output_valid else 'missing/invalid'}",
                    f"Trigger files: {len(self._trigger_files)}",
                    f"Listfiles: {listfile_count}",
                    f"Enabled items: {'yes' if has_enabled_entries else 'no'}",
                    f"Loaded target: {'map' if self._loaded_map_text else 'campaign' if self._campaign_context else 'not loaded'}",
                    f"Selected maps: {selected_campaign_maps if campaign_mode else 'n/a'}",
                ]
            )
        )
        self.status_file_label.setText(
            f"File state: {'ready' if input_valid and output_valid else 'blocked'}"
        )

        self.remove_trigger_button.setEnabled(self.trigger_file_list.currentRow() >= 0 and not worker_running)
        self.add_listfile_button.setEnabled(not worker_running)
        self.remove_listfile_button.setEnabled(self.listfile_list.currentRow() >= 0 and not worker_running)
        self.load_input_button.setEnabled(input_valid and not worker_running)
        self.scan_trigger_button.setEnabled(has_trigger_files and not worker_running)
        self.validate_button.setEnabled((input_valid or has_enabled_entries) and not worker_running)
        self.inject_button.setEnabled(
            input_valid
            and output_valid
            and has_enabled_entries
            and (
                (campaign_mode and selected_campaign_maps > 0 and self._campaign_context is not None)
                or (not campaign_mode)
            )
            and not worker_running
        )
        self.refresh_campaign_button.setEnabled(campaign_mode and input_valid and not worker_running)
        self.select_all_maps_button.setEnabled(campaign_mode and bool(self.campaign_map_table.entries()) and not worker_running)
        self.unselect_all_maps_button.setEnabled(campaign_mode and bool(self.campaign_map_table.entries()) and not worker_running)
        self.campaign_failure_combo.setEnabled(campaign_mode and not worker_running)
        self.save_preset_button.setEnabled(not worker_running)
        self.load_preset_button.setEnabled(not worker_running)
        self.reset_button.setEnabled(not worker_running)

    def _append_log(self, severity: str, message: str) -> None:
        self.log_panel.append_log(severity, message)


class Worker(QObject):
    """Background worker for scan/load/build tasks."""

    progressChanged = Signal(str)
    logEmitted = Signal(str, str)
    scanCompleted = Signal(object)
    inputLoaded = Signal(object)
    injectBuildCompleted = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, mode: str, **kwargs) -> None:
        super().__init__()
        self.mode = mode
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            if self.mode == "scan-triggers":
                self._run_scan_triggers()
            elif self.mode == "load-input":
                self._run_load_input()
            elif self.mode == "scan-campaign":
                self._run_scan_campaign()
            else:
                self._run_inject_build()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    def _run_scan_triggers(self) -> None:
        self.progressChanged.emit("scanning trigger files")
        imported_results: list[TriggerImportResult] = []
        for trigger_file in self.kwargs["trigger_files"]:
            self.logEmitted.emit("INFO", f"Scanning trigger file: {trigger_file}")
            imported_results.append(parse_trigger_file(trigger_file))
        self.progressChanged.emit("extracting patch parts")
        self.scanCompleted.emit(imported_results)

    def _run_load_input(self) -> None:
        from services.campaign_loader import list_campaign_maps, load_campaign_source
        from services.map_loader import load_map_source

        input_path = self.kwargs["input_path"]
        input_type = detect_input_type(input_path)
        external_listfiles = self.kwargs.get("external_listfiles")
        if input_type.is_map:
            context = load_map_source(
                input_path,
                external_listfiles=external_listfiles,
                progress_callback=self.progressChanged.emit,
                log_callback=self.logEmitted.emit,
            )
            self.inputLoaded.emit(
                {
                    "input_type": input_type,
                    "map_context": context,
                }
            )
            return

        campaign_context = load_campaign_source(
            input_path,
            external_listfiles=external_listfiles,
            progress_callback=self.progressChanged.emit,
            log_callback=self.logEmitted.emit,
        )
        campaign_maps = list_campaign_maps(
            campaign_context,
            progress_callback=self.progressChanged.emit,
            log_callback=self.logEmitted.emit,
        )
        self.inputLoaded.emit(
            {
                "input_type": input_type,
                "campaign_context": campaign_context,
                "campaign_maps": campaign_maps,
            }
        )

    def _run_scan_campaign(self) -> None:
        from services.campaign_loader import list_campaign_maps

        campaign_context = self.kwargs["campaign_context"]
        campaign_maps = list_campaign_maps(
            campaign_context,
            progress_callback=self.progressChanged.emit,
            log_callback=self.logEmitted.emit,
        )
        self.inputLoaded.emit(
            {
                "input_type": InputType.CAMPAIGN_W3N,
                "campaign_context": campaign_context,
                "campaign_maps": campaign_maps,
            }
        )

    def _run_inject_build(self) -> None:
        from services.injector import inject_and_build

        input_type = self.kwargs["input_type"] or detect_input_type(self.kwargs["input_path"])
        if input_type.is_map:
            result = inject_and_build(
                input_map=self.kwargs["input_path"],
                output_map=self.kwargs["output_path"],
                selected_patch=self.kwargs["selection"],
                options=self.kwargs["options"],
                external_listfiles=self.kwargs.get("external_listfiles"),
                progress_callback=self.progressChanged.emit,
                log_callback=self.logEmitted.emit,
            )
            self.injectBuildCompleted.emit(result)
            return

        summary = inject_and_build_campaign(
            campaign_context=self.kwargs["campaign_context"],
            output_campaign=self.kwargs["output_path"],
            selected_patch=self.kwargs["selection"],
            selected_maps=self.kwargs["campaign_maps"],
            options=self.kwargs["options"],
            progress_callback=self.progressChanged.emit,
            log_callback=self.logEmitted.emit,
        )
        self.injectBuildCompleted.emit(summary)


def _guess_global_name(text: str) -> str:
    parts = text.split()
    if "=" in parts:
        index = parts.index("=")
        if index > 0:
            return parts[index - 1]
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else ""


def _guess_function_name(signature_line: str) -> str:
    parts = signature_line.strip().split()
    if len(parts) >= 2:
        return parts[1]
    return ""


def _guess_call_name(text: str) -> str:
    candidate = text.strip()
    if candidate.startswith("call "):
        candidate = candidate[5:]
    if "(" in candidate:
        candidate = candidate.split("(", 1)[0]
    return candidate.strip()
