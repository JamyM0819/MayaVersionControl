"""
ui/history_browser.py - Version history browser.
Simple QWidget with Qt.Window flag, parented to Maya main window.
"""

import os
import re
import json
import subprocess
import datetime
import textwrap as _textwrap

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QAbstractItemView,
    QToolButton, QMenu, QLineEdit, QWidgetAction, QFileDialog,
    QDialog, QTextEdit, QStyledItemDelegate,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QStyle
from shiboken6 import isValid as _isValid

from core.vc_engine import (get_scenes_dir, get_history, load_version, delete_version,
                              _parse_ver, get_plugin_repo_hash, vc_amend_commit,
                              dry_run_next_version, vc_commit,
                              _acquire_and_read, _write_versions_atomic, _unlock_file)
from core.perf_monitor import show_perf_panel
from ui.commit_dialog import show_commit_dialog, show_amend_dialog


# ---------------------------------------------------------------------------
# Persistent collapsed-state storage (survives Maya sessions)
# ---------------------------------------------------------------------------
_COLLAPSED_STATE = {}  # tag -> True/False, in-memory; write to Maya optionVar


def _load_collapsed():
    """Clear in-memory collapsed state. Actual state is loaded from panel JSON."""
    global _COLLAPSED_STATE
    _COLLAPSED_STATE = {}


def _save_collapsed():
    """No-op. Collapsed state is persisted via _save_panel_state to JSON."""


# ---------------------------------------------------------------------------
# Persistent recent projects (survives Maya sessions)
# ---------------------------------------------------------------------------


def _load_recent_projects():
    """Load recent project paths from Maya optionVars. Returns list of paths."""
    try:
        import maya.cmds as cmds
        raw = cmds.optionVar(q="MayaVC_recent_projects") or ""
        paths = [p for p in raw.split(";;") if p and os.path.isdir(p)]
        return paths
    except Exception:
        return []


def _save_recent_projects(paths):
    """Persist recent project paths to Maya optionVars (max 10)."""
    try:
        import maya.cmds as cmds
        deduped = list(dict.fromkeys(paths))[:10]
        cmds.optionVar(sv=("MayaVC_recent_projects", ";;".join(deduped)))
    except Exception:
        pass


def _add_recent_project(path):
    """Add a path to the front of recent projects list and persist."""
    paths = _load_recent_projects()
    path = os.path.normpath(os.path.abspath(path))
    if path in paths:
        paths.remove(path)
    paths.insert(0, path)
    _save_recent_projects(paths)


# ---------------------------------------------------------------------------
# Persistent window geometry (survives Maya sessions)
# ---------------------------------------------------------------------------

def _load_geometry():
    """Restore previous window position/size from Maya optionVars.
    Returns (x, y, w, h) or None."""
    try:
        import maya.cmds as cmds
        x = cmds.optionVar(q="MayaVC_win_x")
        y = cmds.optionVar(q="MayaVC_win_y")
        w = cmds.optionVar(q="MayaVC_win_w")
        h = cmds.optionVar(q="MayaVC_win_h")
        if all(v is not None for v in (x, y, w, h)):
            return (int(x), int(y), int(w), int(h))
    except Exception:
        pass
    return None


def _save_geometry(x, y, w, h):
    """Persist window position/size to Maya optionVars as 4 plain ints."""
    try:
        import maya.cmds as cmds
        cmds.optionVar(iv=("MayaVC_win_x", int(x)))
        cmds.optionVar(iv=("MayaVC_win_y", int(y)))
        cmds.optionVar(iv=("MayaVC_win_w", int(w)))
        cmds.optionVar(iv=("MayaVC_win_h", int(h)))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


def _get_maya_window():
    """Return Maya main window as a QWidget."""
    try:
        from shiboken6 import wrapInstance
        import maya.OpenMayaUI as omui
        ptr = omui.MQtUtil.mainWindow()
        if ptr:
            return wrapInstance(int(ptr), QWidget)
    except Exception:
        pass
    return None


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by a custom sort key instead of display text."""
    __slots__ = ("_sort_key",)

    def __init__(self, text, sort_key):
        super().__init__(text)
        self._sort_key = sort_key

    def __lt__(self, other):
        if isinstance(other, _SortableItem):
            return self._sort_key < other._sort_key
        return super().__lt__(other)


def _saveable_state(st):
    """Return a dict with only JSON-safe keys from the panel state."""
    keys = ("sort_dir_0", "sort_dir_1", "sort_dir_2", "sort_dir_3",
            "_last_sort_section", "_record_order",
            "filter_mode", "latest_only", "scenes_dir",
            "_scroll", "_sel_tag", "_collapsed")
    return {k: st[k] for k in keys if k in st}


def _collect_and_save_from_window(win):
    """Collect current panel state from a window and persist to JSON."""
    try:
        if not _isValid(win):
            return
        st = getattr(win, "_mayavc_state", None)
        tbl = getattr(win, "_mayavc_table", None)
        if st is None or tbl is None:
            return
        st["_scroll"] = tbl.verticalScrollBar().value()
        sel = tbl.selectedItems()
        st["_sel_tag"] = _tag_for_row(tbl.row(sel[0])) if sel else ""
        st["_collapsed"] = dict(_COLLAPSED_STATE)
        _save_panel_state(_saveable_state(st))
    except Exception:
        pass
        pass


def show():
    """Show the history browser. Always creates a fresh window."""
    print("[MayaVC] LOADED — fix4 (2026-06-28)")
    _load_collapsed()

    mw = _get_maya_window()
    if mw is None:
        return

    # Close any existing history window first — save state and geometry before closing
    if hasattr(show, "_windows"):
        for w in show._windows[:]:
            try:
                if _isValid(w):
                    # Save panel state (sort, filter, scroll, selection, collapsed)
                    _collect_and_save_from_window(w)
                    # Save position/size before closing
                    pos = w.pos()
                    sz = w.size()
                    _save_geometry(pos.x(), pos.y(), sz.width(), sz.height())
                    w.close()
            except Exception:
                pass
        show._windows.clear()

    # Scenes dir — always use current Maya scene dir, fallback to cached
    d = ""
    try:
        from maya import cmds
        cur = cmds.file(query=True, sceneName=True)
        if cur:
            d = os.path.dirname(cur)
    except Exception:
        pass
    if not d or not os.path.isdir(d):
        d = getattr(show, "_last_scenes_dir", "") or ""
    if not d or not os.path.isdir(d):
        try:
            d = get_scenes_dir()
        except Exception:
            d = os.getcwd()
    show._last_scenes_dir = d

    # ---- window ----
    win = QWidget(mw, Qt.Window)
    win.setObjectName("MayaVCHistoryWidget")
    win.setWindowTitle("MayaVersionControl")

    # Restore previous geometry if available
    geo = _load_geometry()
    if geo:
        x, y, w, h = geo
        win.setGeometry(x, y, w, h)
    else:
        win.resize(900, 550)

    win.setMinimumSize(1000, 350)

    lay = QVBoxLayout(win)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.setSpacing(4)

    top_bar = QHBoxLayout()

    # ---- Set Project (button + dropdown + folder icon) ----
    proj_row = QHBoxLayout()
    set_project_btn = QPushButton("(none)")
    set_project_btn.setMinimumWidth(140)
    set_project_btn.setStyleSheet("text-align: left; padding-left: 8px;")
    set_project_menu = QMenu(set_project_btn)
    set_project_btn.setMenu(set_project_menu)

    # Folder icon button to the right of the project button
    folder_btn = QPushButton("📂")
    folder_btn.setToolTip("Browse for project folder")
    folder_btn.setFixedWidth(32)
    folder_btn.setStyleSheet("font-size: 16px;")

    proj_row.addWidget(set_project_btn)
    proj_row.addWidget(folder_btn)
    top_bar.addLayout(proj_row)
    top_bar.addSpacing(8)

    # Build the popup menu (rebuilt each time it's about to show)
    def _build_project_menu():
        set_project_menu.clear()

        # -- Search/filter bar at top --
        search_action = QWidgetAction(set_project_menu)
        search_box = QLineEdit()
        search_box.setPlaceholderText("Filter...")
        search_box.setClearButtonEnabled(True)
        search_action.setDefaultWidget(search_box)
        set_project_menu.addAction(search_action)

        # -- Recent projects section --
        recent = _load_recent_projects()
        current_dir = state.get("scenes_dir", "")
        items = []
        if current_dir and os.path.isdir(current_dir):
            name = os.path.basename(current_dir)
            if name.lower() == "scenes":
                name = os.path.basename(os.path.dirname(current_dir)) or name
            items.append((name, current_dir))
        for p in recent:
            p = os.path.normpath(p)
            if p and os.path.isdir(p) and p != current_dir:
                name = os.path.basename(p)
                if name.lower() == "scenes":
                    name = os.path.basename(os.path.dirname(p)) or name
                items.append((name, p))
        items = list(dict.fromkeys(items))

        def _filter_menu(text):
            filt = text.lower()
            for action in set_project_menu.actions():
                if action is search_action:
                    continue
                if action.isSeparator():
                    action.setVisible(not filt)
                else:
                    action.setVisible(not filt or filt in action.text().lower())

        search_box.textChanged.connect(_filter_menu)

        if items:
            if current_dir and os.path.isdir(current_dir):
                set_project_menu.addSection("Current")
                for label, path in items[:1]:
                    a = set_project_menu.addAction(f"  {label}")
                    a.setToolTip(path)
                    a.triggered.connect(lambda checked=False, p=path: _on_set_project(p))
            rest = items[1:] if (current_dir and os.path.isdir(current_dir)) else items
            if rest:
                set_project_menu.addSection("Recent")
                for label, path in rest:
                    a = set_project_menu.addAction(f"  {label}")
                    a.setToolTip(path)
                    a.triggered.connect(lambda checked=False, p=path: _on_set_project(p))

        set_project_menu.addSeparator()

        snap_a = set_project_menu.addAction("📍  Use Current Maya Project")
        snap_a.triggered.connect(_on_use_current_maya_project)

    set_project_menu.aboutToShow.connect(_build_project_menu)

    def _update_project_button_text():
        d = state.get("scenes_dir", "")
        if d and os.path.isdir(d):
            # If path ends with /scenes, show parent folder name (the project)
            name = os.path.basename(d) or d
            if name.lower() == "scenes":
                parent = os.path.dirname(d)
                name = os.path.basename(parent) or name
            set_project_btn.setText(name)
            set_project_btn.setToolTip(d)
        else:
            set_project_btn.setText("(none)")
            set_project_btn.setToolTip("")

    def _on_set_project(path):
        if not path or not os.path.isdir(path):
            import maya.cmds as cmds
            cmds.warning("MayaVC: Project directory not found.")
            return
        state["scenes_dir"] = path
        _add_recent_project(path)
        _update_project_button_text()
        do_refresh()

    def _on_browse_project():
        d = QFileDialog.getExistingDirectory(
            win, "Select Scenes Directory",
            state.get("scenes_dir", os.getcwd()),
            QFileDialog.ShowDirsOnly,
        )
        if d:
            scenes = os.path.join(d, "scenes")
            if os.path.isdir(scenes):
                d = scenes
            _on_set_project(d)

    def _on_use_current_maya_project():
        import maya.cmds as cmds
        try:
            d = get_scenes_dir()
            if d and os.path.isdir(d):
                _on_set_project(d)
            else:
                cmds.warning("MayaVC: Cannot determine current Maya project.")
        except Exception as e:
            cmds.warning(f"MayaVC: {e}")

    # Segment: 历史版本 | 只看最新
    seg1 = QWidget()
    seg1_lay = QHBoxLayout(seg1)
    seg1_lay.setContentsMargins(0, 0, 0, 0)
    seg1_lay.setSpacing(0)
    latest_all_btn = QPushButton("历史版本")
    latest_new_btn = QPushButton("只看最新")
    seg1_lay.addWidget(latest_all_btn)
    seg1_lay.addWidget(latest_new_btn)
    top_bar.addWidget(seg1)

    collapse_all_btn = QPushButton("全部展开")
    top_bar.addWidget(collapse_all_btn)

    # Segment: 全部显示 | 只看当前
    seg2 = QWidget()
    seg2_lay = QHBoxLayout(seg2)
    seg2_lay.setContentsMargins(0, 0, 0, 0)
    seg2_lay.setSpacing(0)
    filter_all_btn = QPushButton("全部显示")
    filter_cur_btn = QPushButton("只看当前")
    seg2_lay.addWidget(filter_all_btn)
    seg2_lay.addWidget(filter_cur_btn)
    top_bar.addWidget(seg2)

    active_style = "QPushButton { background-color: #2980b9; color: #fff; font-weight: bold; }"
    dim_style = "QPushButton { background-color: #3a3a3a; color: #999; }"
    latest_all_btn.setStyleSheet(active_style)
    filter_all_btn.setStyleSheet(active_style)
    latest_new_btn.setStyleSheet(dim_style)
    filter_cur_btn.setStyleSheet(dim_style)
    lay.addLayout(top_bar)

    # Info row: project name + version count + current version
    info_bar = QHBoxLayout()
    label = QLabel("")
    info_bar.addWidget(label, stretch=1)
    info_label = QLabel("")
    info_label.setStyleSheet("font-size: 12px; font-weight: bold;")
    info_label.setTextFormat(Qt.RichText)
    info_label.setCursor(Qt.PointingHandCursor)
    info_bar.addWidget(info_label)
    lay.addLayout(info_bar)

    table = QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(["Name", "Version", "Date", "Message"])
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setShowGrid(True)
    table.setStyleSheet("QTableWidget { gridline-color: #262626; }")
    table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.verticalHeader().setVisible(False)
    hdr = table.horizontalHeader()
    hdr.setSectionResizeMode(0, QHeaderView.Interactive)
    hdr.setSectionResizeMode(1, QHeaderView.Interactive)
    hdr.setSectionResizeMode(2, QHeaderView.Interactive)
    hdr.setSectionResizeMode(3, QHeaderView.Stretch)
    table.setColumnWidth(0, 130)
    table.setColumnWidth(1, 60)
    table.setColumnWidth(2, 130)
    table.setColumnWidth(3, 280)
    table.setWordWrap(True)
    lay.addWidget(table, stretch=1)

    # We handle all sorting manually.  Sorting stays OFF so Qt never
    # auto-sorts during do_refresh.  Sort indicators are set explicitly
    # via setSortIndicator() after each sort and stay visible as long as
    # setSortingEnabled(False) is not called again (Qt 5.15+ quirk).
    table.setSortingEnabled(False)

    # Custom delegate: paint 10px green bar for current version row
    class GreenBarDelegate(QStyledItemDelegate):
        def paint(self, painter, option, index):
            # Column 3: multi-line with separator coloring
            if index.column() == 3:
                text = index.data(Qt.DisplayRole) or ""
                if "\n" in text and any(c in text for c in "┈─"):
                    # Paint background — use item's actual background color
                    bg = index.data(Qt.BackgroundRole)
                    if bg is None:
                        bg = option.palette.highlight() if option.state & QStyle.State_Selected else option.palette.base()
                    painter.fillRect(option.rect, bg)
                    # Draw lines
                    lines = text.split("\n")
                    sep_color = QColor("#5c5b5b")
                    normal = option.palette.text().color()
                    fm = painter.fontMetrics()
                    y = option.rect.y() + fm.ascent()
                    for line in lines:
                        painter.setPen(sep_color if line.strip() and line.strip()[0] in "┈─" else normal)
                        painter.drawText(option.rect.x() + 4, y, line)
                        y += fm.height()
                    return
            # Default + green bar for current version
            super().paint(painter, option, index)
            if index.column() == 0 and index.data(Qt.UserRole + 1):
                painter.fillRect(option.rect.x(), option.rect.y(),
                                 10, option.rect.height(), QColor("#27AE60"))
    table.setItemDelegate(GreenBarDelegate(table))

    # Two group colours that alternate per-base-name when rows are sorted by
    # Version (i.e. grouped by base name).  Cleared on Date / Message sort.
    _GROUP_COLOURS = [
        QColor("#474747"),  # warm ivory
        QColor("#383838"),  # cool grey
    ]

    blay = QHBoxLayout()
    # left group: save actions
    inc_save_btn = QPushButton("Incremental Save")
    blay.addWidget(inc_save_btn)
    save_commit_btn = QPushButton("Save w/ Commit")
    blay.addWidget(save_commit_btn)
    blay.addStretch()
    # right group: tools
    delete_btn = QPushButton("Delete This Version")
    delete_btn.setEnabled(False)
    blay.addWidget(delete_btn)
    edit_btn = QPushButton("Edit")
    blay.addWidget(edit_btn)
    delete_selected_btn = QPushButton("Delete Selected")
    delete_selected_btn.setEnabled(False)
    blay.addWidget(delete_selected_btn)
    clear_desc_btn = QPushButton("Clear Descriptions")
    clear_desc_btn.setEnabled(False)
    blay.addWidget(clear_desc_btn)
    blay.addStretch()
    refresh_btn = QPushButton("Refresh")
    blay.addWidget(refresh_btn)
    # "?" help button — replaces Clear Cache + Perf
    help_btn = QToolButton()
    help_btn.setText("?")
    help_btn.setFixedWidth(28)
    help_btn.setToolTip("Tools")
    blay.addWidget(help_btn)
    lay.addLayout(blay)

    # Footer: version + author + GitHub link
    footer = QHBoxLayout()
    h = get_plugin_repo_hash() or ""
    ver_text = f"v1.0.4"
    if h:
        ver_text += f"  [{h[:7]}]"
    ver_label = QLabel(ver_text)
    ver_label.setStyleSheet("font-size: 10px; color: #999;")
    footer.addWidget(ver_label)
    footer.addStretch()
    author_label = QLabel("by JamyM")
    author_label.setStyleSheet("font-size: 10px; color: #999;")
    footer.addWidget(author_label)
    github_link = QLabel('<a href="https://github.com/JamyM0819/MayaVersionControl" style="color:#999;">查看更多</a>')
    github_link.setStyleSheet("font-size: 10px;")
    github_link.setTextFormat(Qt.RichText)
    github_link.setOpenExternalLinks(True)
    footer.addWidget(github_link)
    lay.addLayout(footer)

    state = {"records": [], "scenes_dir": d, "filter_mode": "all",
             "latest_only": False, "edit_mode": False, "cur_tag": None}

    # Restore saved panel state (sort, filter, collapse, etc.)
    saved = _load_panel_state()
    for k in ("sort_dir_0", "sort_dir_1", "sort_dir_2", "sort_dir_3",
              "_last_sort_section", "_record_order",
              "filter_mode", "latest_only"):
        if k in saved:
            state[k] = saved[k]
    if "_collapsed" in saved:
        _COLLAPSED_STATE.clear()
        _COLLAPSED_STATE.update(saved["_collapsed"])
    saved_dir = saved.get("scenes_dir", "")
    if saved_dir and os.path.isdir(saved_dir):
        state["scenes_dir"] = saved_dir
    else:
        saved["scenes_dir"] = d
    # Stash for later restore (scroll, selection, etc.)
    state["_saved"] = saved

    _update_project_button_text()

    def _visible_records(records, latest_only):
        """If latest_only, return only the highest-version record per base name."""
        if not latest_only:
            return records
        best = {}
        ver_re = re.compile(r'^(.+)_v(\d{3,})$')
        for r in records:
            # Use display name (what user sees) for grouping, not internal tag
            display = os.path.splitext(r.file)[0] if r.file else r.tag
            m = ver_re.match(display)
            if not m:
                continue
            base = m.group(1)
            ver = int(m.group(2))
            if base not in best or ver > best[base][1]:
                best[base] = (r, ver)
        # Preserve original order (newest-first) but only keep best per base
        selected = set(v[0] for v in best.values())
        return [r for r in records if r in selected]

    def do_refresh(filter_mode=None, latest_only=None):
        # Save current selection tag to restore after rebuild
        sel_tag = None
        rows = {idx.row() for idx in table.selectedIndexes()}
        if rows:
            item = table.item(min(rows), 0)
            sel_tag = item.data(Qt.UserRole) if item else None

        if filter_mode is not None:
            state["filter_mode"] = filter_mode
        if latest_only is not None:
            state["latest_only"] = latest_only

        state["records"] = []
        table.setRowCount(0)
        label.setText("Loading...")
        info_label.setText("")
        d = state["scenes_dir"]

        # Determine current scene's base name for filtering
        scene_name = None
        try:
            import maya.cmds as cmds
            p = cmds.file(q=True, sn=True)
            if p:
                scene_name = _parse_ver(p)[0]
        except Exception:
            pass

        # In "filter_current" mode, only show versions matching current scene base name
        filter_scene = scene_name if state["filter_mode"] == "current" else None

        try:
            state["records"] = get_history(d, filter_scene)
            # Apply pending Version sort if set, otherwise restore last manual sort
            # Apply saved sort order (set by last column click)
            order = state.get("_record_order")
            if order:
                order_set = {tag: i for i, tag in enumerate(order)}
                state["records"].sort(key=lambda r: order_set.get(r.tag, 9999))
            sk = state.pop("_sort_records_key", None)
            if sk:
                state["records"].sort(key=sk)
            # Count always reflects current scene's base name versions
            if scene_name and not filter_scene:
                my_count = sum(1 for r in state["records"]
                              if r.tag.startswith(scene_name + "_v"))
            else:
                my_count = len(state["records"])
            if filter_scene:
                suffix = f" for '{filter_scene}' (filtered)"
            else:
                suffix = f" for '{scene_name}'" if scene_name else ""
            label.setText(
                f"Project: {os.path.basename(d) or d}{suffix}"
                f"  ({my_count} versions)"
            )

            # current version info from the currently open Maya file
            try:
                import maya.cmds as cmds
                p = cmds.file(q=True, sn=True)
                if p:
                    cur_base, cur_ext, cur_ver = _parse_ver(p)
                    cur_tag = None
                    if cur_ver > 0:
                        cur_tag = f"{cur_base}_v{cur_ver:03d}"
                        # Verify this tag actually exists in records
                        if not any(rec.tag == cur_tag for rec in state["records"]):
                            cur_tag = None  # parsed tag not in JSON — try UUID
                    if not cur_tag:
                        # Try UUID match against JSON (handles renamed files)
                        from core.vc_engine import _read_ntfs_uuid
                        cur_uid = _read_ntfs_uuid(p)
                        if cur_uid:
                            for rec in state["records"]:
                                if rec.uuid == cur_uid:
                                    cur_tag = rec.tag
                                    break
                        # UUID not found — fall back to filename match
                        if not cur_tag:
                            cur_fname = os.path.basename(p)
                            for rec in state["records"]:
                                if rec.file == cur_fname:
                                    cur_tag = rec.tag
                                    break
                    if cur_tag:
                        # Look up time from history records
                        cur_time = ""
                        for rec in state["records"]:
                            if rec.tag == cur_tag:
                                cur_time = rec.date
                                break
                        info_label.setText(
                            f'<a href="locate" style="color:#27AE60; text-decoration:none;">'
                            f'Current: {cur_tag}'
                            f'{("  |  " + cur_time) if cur_time else ""}'
                            f'</a>'
                        )
                        state["cur_tag"] = cur_tag
                    else:
                        info_label.setText("Current: (unsaved)")
                        state["cur_tag"] = None
            except Exception:
                info_label.setText("")
                state["cur_tag"] = None
        except Exception as e:
            label.setText(f"Error: {e}")

        visible = _visible_records(state["records"], state["latest_only"])
        state["visible"] = visible
        state["tag_map"] = {r.tag: r for r in visible}
        cur_tag = state.get("cur_tag")
        cur_row = None

        # --- group colours ---
        # Called AFTER table items exist so _apply_group_colours can write
        # to actual cells.
        table.setRowCount(len(visible))
        for i, r in enumerate(visible):
            is_current = cur_tag and r.tag == cur_tag

            display_name = os.path.splitext(r.file)[0] if r.file else (r.tag or "-")
            # Split into name + version: "球体布尔_v003" → ("球体布尔", "v003")
            m = re.match(r'^(.+)_v(\d{3,})$', display_name)
            if m:
                name_part, ver_part = m.group(1), f"v{m.group(2)}"
            else:
                name_part, ver_part = display_name, ""

            tag_item = QTableWidgetItem(name_part)
            tag_item.setData(Qt.UserRole, r.tag)  # real tag for lookups
            tag_item.setTextAlignment(Qt.AlignCenter)
            if is_current:
                tag_item.setData(Qt.UserRole + 1, True)  # green bar marker
            table.setItem(i, 0, tag_item)

            ver_item = QTableWidgetItem(ver_part)
            ver_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 1, ver_item)

            date_item = QTableWidgetItem(r.date or "")
            date_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 2, date_item)

            # Message column: fold multi-line, store full as UserRole
            full_msg = r.message or ""
            folded = _collapsed_for_tag(r.tag, full_msg)
            msg_item = QTableWidgetItem(folded)
            msg_item.setData(Qt.UserRole, full_msg)
            # Auto-imported messages shown in gray italic as "pending" state
            if "(auto-imported)" in full_msg:
                msg_item.setForeground(QColor("#888888"))
                f = msg_item.font()
                f.setItalic(True)
                msg_item.setFont(f)
            table.setItem(i, 3, msg_item)

            # Highlight current version row
            if is_current:
                # Subtle green tint on Name column only
                name_cell = table.item(i, 0)
                name_cell.setBackground(QColor("#2d6a4f"))
                name_cell.setForeground(QColor("#a3e635"))

            # Expand row height for expanded messages (deferred for layout)
            if "\n" in folded:
                ri = i
                QTimer.singleShot(20, lambda r=ri: _safe_resize_row(r))

        # --- group colours (deferred until Qt applies the sort indicator) ---
        # Group colours only for Name/Version sorts
        ls = state.get("_last_sort_section")
        QTimer.singleShot(0, lambda: _apply_group_colours(visible, cur_tag, sort_section=ls))

        # Auto-scroll to current version row
        _scroll_to_current()

        # Restore previous selection
        if sel_tag:
            for i in range(table.rowCount()):
                if _tag_for_row(i) == sel_tag:
                    table.selectRow(i)
                    break

        # Deferred rewrap: column width may not be final during initial layout.
        QTimer.singleShot(0, _rewrap_all_messages)

    def _msg_col_chars():
        """Return chars that fit in the Message column at current width."""
        fm = table.fontMetrics()
        col_w = table.columnWidth(3)
        # Use widest CJK char width — Chinese chars are ~2x as wide as Latin
        cjk_w = fm.horizontalAdvance("█")
        if cjk_w <= 0:
            cjk_w = fm.averageCharWidth() or 7
        pad = 12
        avail = max(10, col_w - pad)
        return max(15, int(avail / cjk_w))

    def _safe_resize_row(row):
        try:
            if row < table.rowCount():
                table.resizeRowToContents(row)
        except Exception:
            pass

    def _split_ts_body(line):
        """Split a message line into (timestamp_prefix, body_text).

        Returns (ts, body) if line starts with '[YYYY-MM-DD HH:MM] ',
        otherwise (None, line).  The timestamp prefix is stripped from body.
        """
        m = re.match(r'^(\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] )', line)
        if m:
            return m.group(1), line[m.end():]
        return None, line

    def _visual_width(s):
        """Return visual char width — CJK/fullwidth chars count as 2."""
        w = 0
        for ch in s:
            if '一' <= ch <= '鿿' or '　' <= ch <= '〿':
                w += 2
            elif '＀' <= ch <= '￯':
                w += 2
            else:
                w += 1
        return w

    def _wrap_body(body, wrap_chars, indent_str):
        """Wrap body text to fit column width, with indent_str on every line."""
        indent_vis = _visual_width(indent_str)
        w = max(10, wrap_chars - indent_vis)
        lines = body.split("\n")
        out = []
        for ln in lines:
            if not ln.strip():
                out.append(indent_str)
                continue
            wrapped = _textwrap.wrap(ln, width=w)
            out.extend(indent_str + ww for ww in wrapped)
        return "\n".join(out)

    def _build_msg_display(full_msg, collapsed, wrap_chars):
        """Build display text.
        \n\n = amend boundary.  \n alone = body newline within one commit.
        """
        # Detect multi-commit: messages joined by \x1e (record separator)
        if "\x1e" in full_msg:
            commits = full_msg.split("\x1e")
            if collapsed:
                # Show newest commit only, single-line
                ts, body = _split_ts_body(commits[-1])
                body_flat = body.replace("\n", " ").strip() if body else ""
                return ("▶ " + ts.rstrip() if ts else "▶ ") + body_flat[:wrap_chars]
            else:
                # Show all commits, newest first, with separator lines
                out = []
                sep = "┈" * (wrap_chars // 2)
                for i, c in enumerate(reversed(commits)):
                    ts, body = _split_ts_body(c)
                    prefix = "▼ " if i == 0 else "   "
                    if ts:
                        out.append(prefix + ts.rstrip() + "\n" + body.strip())
                    else:
                        out.append(prefix + c.strip())
                    if i < len(commits) - 1:
                        out.append(sep)
                return "\n".join(out)
        
        # Single commit
        ts, body = _split_ts_body(full_msg)
        if collapsed:
            body_flat = body.replace("\n", " ").strip() if body else ""
            if "\n" in full_msg:
                return ("▶ " + ts.rstrip() + " " + body_flat)[:wrap_chars] if ts else ("▶ " + full_msg[:wrap_chars - 2])
            return (ts.rstrip() + " " + body_flat)[:wrap_chars] if ts else full_msg[:wrap_chars]
        else:
            prefix = "▼ " if "\n" in full_msg else ""
            return (prefix + ts.rstrip() + "\n" + body.strip()) if ts else full_msg

    def _apply_group_colours(visible, cur_tag, sort_section=-1):
        """Paint alternating backgrounds per base-name group when sorted by
        Version (section 0).  Disable group colours on Date/Message sort."""

        if not visible:
            return

        # ------ decide whether group colours should be ON ------
        if sort_section is None or sort_section < 0:
            sort_section = table.horizontalHeader().sortIndicatorSection()
        row_count = table.rowCount()
        if row_count == 0:
            return

        if sort_section in (0, 1):
            # Walk table rows and collect first occurrence of each Name.
            # Name column (0) already contains just the base, no version.
            seen = set()
            bases_in_order = []
            for i in range(row_count):
                name_item = table.item(i, 0)
                base = name_item.text() if name_item else ""
                if base and base not in seen:
                    seen.add(base)
                    bases_in_order.append(base)
            base_order = {b: idx for idx, b in enumerate(bases_in_order)}

            for i in range(row_count):
                tag = _tag_for_row(i)
                name_item = table.item(i, 0)
                base = name_item.text() if name_item else ""
                c = _GROUP_COLOURS[base_order.get(base, 0) % len(_GROUP_COLOURS)]
                for col in range(4):
                    cell = table.item(i, col)
                    if cell:
                        cell.setBackground(c)
        else:
            # Date (1) or Message (2) sort — clear all non-current backgrounds
            for i in range(row_count):
                tag = _tag_for_row(i)
                for col in range(4):
                    cell = table.item(i, col)
                    if cell:
                        cell.setBackground(QColor(255, 255, 255, 0))  # transparent

    def _rewrap_all_messages():
        """Re-wrap all visible message texts for current column width."""
        row_count = table.rowCount()
        if row_count == 0:
            return
        w = _msg_col_chars()
        tag_map = state.get("tag_map", {})
        for i in range(row_count):
            tag = _tag_for_row(i)
            if not tag:
                continue
            r = tag_map.get(tag)
            if not r:
                continue
            full_msg = r.message or ""
            new_text = _collapsed_for_tag(r.tag, full_msg, wrap_chars=w)
            item = table.item(i, 3)
            if item:
                if item.text() != new_text:
                    item.setText(new_text)
                ri = i
                QTimer.singleShot(20, lambda r=ri: _safe_resize_row(r))

    def _on_msg_col_resized(col, old_w, new_w):
        if col == 3 and new_w != old_w:
            _rewrap_all_messages()

    def _on_section_clicked(section):
        """Handle all column clicks when Qt sorting is disabled."""
        # Save selected tag before sorting
        sel_tag = None
        rows = {idx.row() for idx in table.selectedIndexes()}
        if rows:
            sel_tag = _tag_for_row(min(rows))

        if section == 1:
            # Pre-scan Name order for Version sort
            state["_pre_click_names"] = []
            for i in range(table.rowCount()):
                n = table.item(i, 0).text() if table.item(i, 0) else ""
                state["_pre_click_names"].append(n)
        # Toggle direction for this section
        dir_key = f"sort_dir_{section}"
        state[dir_key] = not state.get(dir_key, False)
        state["_last_sort_section"] = section
        order = Qt.DescendingOrder if state[dir_key] else Qt.AscendingOrder
        # Trigger the sort
        _on_sort_changed(section, order)

        # Restore selection + scroll to it
        if sel_tag:
            for i in range(table.rowCount()):
                if _tag_for_row(i) == sel_tag:
                    table.selectRow(i)
                    table.scrollToItem(table.item(i, 0), QTableWidget.PositionAtCenter)
                    break

    def _update_sort_header(section, order):
        """Add ▲/▼ to the sorted column's header label."""
        base = ["Name", "Version", "Date", "Message"]
        labels = list(base)
        arrow = " ▲" if order == Qt.AscendingOrder else " ▼"
        labels[section] = base[section] + arrow
        table.setHorizontalHeaderLabels(labels)

    def _on_sort_changed(section, order):
        """Version (1) = apply pre-scanned Name order sort.
        Other columns use Qt default."""
        if section == 1:
            if getattr(_on_sort_changed, "_busy", False):
                return
            _on_sort_changed._busy = True
            try:
                state["_last_sort_section"] = 1
                desc = (order == Qt.DescendingOrder)

                # Build name_order from pre-scanned names
                pre_names = state.pop("_pre_click_names", [])
                name_order = {}
                next_idx = 0
                for n in pre_names:
                    if n and n not in name_order:
                        name_order[n] = next_idx
                        next_idx += 1

                def _sk(r):
                    n = os.path.splitext(r.file)[0] if r.file else r.tag
                    m = re.match(r'^(.+)_v(\d+)$', n)
                    name, ver = (m.group(1), int(m.group(2))) if m else (n, 0)
                    return (name_order.get(name, 9999), -ver if desc else ver)

                state["_sort_records_key"] = _sk
                do_refresh(latest_only=state["latest_only"])
                state["_record_order"] = [_tag_for_row(i) for i in range(table.rowCount())]
                table.horizontalHeader().setSortIndicator(1, order)
                _update_sort_header(1, order)
            finally:
                _on_sort_changed._busy = False
            vis = state.get("visible", [])
            ct = state.get("cur_tag")
            if vis:
                QTimer.singleShot(0, lambda: _apply_group_colours(vis, ct, sort_section=1))
            return
        # Manual sort for Name (0), Date (2), Message (3)
        if section in (0, 2, 3):
            rev = (order == Qt.DescendingOrder)
            row_count = table.rowCount()
            rows = []
            for i in range(row_count):
                txt = table.item(i, section).text() if table.item(i, section) else ""
                rows.append((txt.lower(), i))
            rows.sort(reverse=rev)
            all_items = []
            for i in range(row_count):
                all_items.append([table.takeItem(i, c) for c in range(4)])
            for tgt, (_, src) in enumerate(rows):
                for c in range(4):
                    item = all_items[src][c]
                    if item is not None:
                        table.setItem(tgt, c, item)
            table.horizontalHeader().setSortIndicator(section, order)
            _update_sort_header(section, order)
            # Save current order for restoration after do_refresh
            state["_record_order"] = [_tag_for_row(i) for i in range(table.rowCount())]
            # Fix row heights after reorder
            QTimer.singleShot(0, _rewrap_all_messages)
        visible = state.get("visible")
        cur_tag = state.get("cur_tag")
        if visible:
            QTimer.singleShot(0, lambda: _apply_group_colours(visible, cur_tag, sort_section=section))

    def _collapsed_for_tag(tag, full_msg, wrap_chars=None):
        """Return display text, respecting collapse state."""
        if wrap_chars is None:
            wrap_chars = _msg_col_chars()
        collapsed = _COLLAPSED_STATE.get(tag, True)
        return _build_msg_display(full_msg, collapsed, wrap_chars)

    def _tag_for_row(row):
        """Get the tag (JSON key) for a visual table row."""
        item = table.item(row, 0)
        if not item:
            return ""
        return item.data(Qt.UserRole) or item.text()

    def _scroll_to_current():
        """Scroll to the current version row without selecting it."""
        cur_tag = state.get("cur_tag")
        if not cur_tag:
            return
        row_count = table.rowCount()
        for i in range(row_count):
            if _tag_for_row(i) == cur_tag:
                table.scrollToItem(table.item(i, 0), QTableWidget.PositionAtCenter)
                break

    def on_msg_click(item):
        """Toggle collapse/expand for message column on double-click."""
        if item.column() != 3:
            return
        row = item.row()
        tag = _tag_for_row(row)
        if not tag:
            return
        r = state.get("tag_map", {}).get(tag)
        if not r:
            return
        full_msg = r.message or ""
        # Allow expansion for multi-commit messages (\x1e) even when no
        # embedded newlines exist.  The expanded display adds \n at render time.
        if "\n" not in full_msg and "\x1e" not in full_msg:
            return

        # Toggle
        cur = _COLLAPSED_STATE.get(tag, True)
        _COLLAPSED_STATE[tag] = not cur
        _save_collapsed()

        # Update the item text immediately
        new_text = _collapsed_for_tag(tag, full_msg, wrap_chars=_msg_col_chars())
        msg_item = table.item(item.row(), 3)
        msg_item.setText(new_text)
        ri = item.row()
        QTimer.singleShot(20, lambda r=ri: _safe_resize_row(r))

    table.itemClicked.connect(on_msg_click)
    table.itemDoubleClicked.connect(lambda _: None if state.get("edit_mode") else on_load())

    # Click on empty area → clear selection
    def on_table_clicked(index):
        if not index.isValid():
            table.clearSelection()
    table.clicked.connect(on_table_clicked)

    def on_sel():
        if state["edit_mode"]:
            rows = {idx.row() for idx in table.selectedIndexes()}
            en = bool(rows) and min(rows) < table.rowCount()
            delete_selected_btn.setEnabled(en)
            clear_desc_btn.setEnabled(en)
        else:
            rows = {idx.row() for idx in table.selectedIndexes()}
            en = bool(rows) and min(rows) < table.rowCount()
            delete_btn.setEnabled(en)

    def on_edit():
        if state["edit_mode"]:
            # Exit edit mode
            edit_btn.setText("Edit")
            edit_btn.setStyleSheet("")
            delete_selected_btn.setEnabled(False)
            clear_desc_btn.setEnabled(False)
            delete_btn.setEnabled(True)
            table.setSelectionMode(QAbstractItemView.SingleSelection)
            state["edit_mode"] = False
        else:
            # Enter edit mode
            edit_btn.setText("Done")
            edit_btn.setStyleSheet("background-color: #e74c3c; color: #fff; font-weight: bold;")
            delete_selected_btn.setEnabled(True)
            clear_desc_btn.setEnabled(True)
            delete_btn.setEnabled(False)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            state["edit_mode"] = True

    def on_delete_selected():
        rows = sorted({idx.row() for idx in table.selectedIndexes()})
        if not rows:
            return
        tag_map = state.get("tag_map", {})
        selected = []
        for i in rows:
            tag = _tag_for_row(i)
            if tag:
                r = tag_map.get(tag)
                if r:
                    selected.append(r)
        if not selected:
            return

        tag_list = "\n".join(f"  {r.tag}  ({r.date})" for r in selected)
        import maya.cmds as cmds
        import random
        count = len(selected)
        code = str(random.randint(100, 999))
        result = cmds.promptDialog(
            title="⚠  DELETE MULTIPLE VERSIONS — IRREVERSIBLE",
            message=(
                f"Permanently delete {count} versions?\n\n"
                f"{tag_list}\n\n"
                f"⚠ {count} files will be deleted from disk.\n"
                f"   Type {code} to confirm:"
            ),
            text="",
            button=["Confirm", "Cancel"],
            defaultButton="Cancel",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if result != "Confirm":
            return
        entered = cmds.promptDialog(q=True, text=True) or ""
        if entered.strip() != code:
            cmds.warning(f"MayaVC: delete cancelled (code mismatch)")
            return
        failed = 0
        for r in selected:
            if not delete_version(state["scenes_dir"], r.tag, r.file):
                failed += 1
        if failed:
            cmds.warning(f"MayaVC: {failed} of {len(selected)} deletions failed")
        do_refresh()
        # Auto-exit edit mode after delete
        if state.get("edit_mode"):
            on_edit()

    def on_clear_descriptions():
        """Clear descriptions for all selected versions."""
        rows = sorted({idx.row() for idx in table.selectedIndexes()})
        if not rows:
            return
        tag_map = state.get("tag_map", {})
        selected = []
        for i in rows:
            tag = _tag_for_row(i)
            if tag:
                r = tag_map.get(tag)
                if r:
                    selected.append(r)
        if not selected:
            return

        import maya.cmds as cmds
        count = len(selected)
        tag_list = "\n".join(f"  {r.tag}  ({r.date})" for r in selected)
        result = cmds.confirmDialog(
            title="Clear Descriptions",
            message=f"Clear descriptions for {count} versions?\n\n{tag_list}\n\nFiles will NOT be deleted.",
            button=["Clear", "Cancel"],
            defaultButton="Cancel",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if result != "Clear":
            return

        d = state["scenes_dir"]
        lock, data = _acquire_and_read(d)
        try:
            cleared = 0
            for r in selected:
                tag = r.tag
                if tag in data:
                    data[tag]["messages"] = []
                    cleared += 1
            if cleared:
                _write_versions_atomic(d, lock, data)
                cmds.warning(f"MayaVC: cleared descriptions for {cleared} versions")
        finally:
            _unlock_file(lock)
        do_refresh()

    def on_save_commit():
        """Save current Maya scene and append a commit to the current version tag."""
        import maya.cmds as cmds
        import maya.mel as mel

        cur = cmds.file(q=True, sn=True)
        if not cur:
            cmds.warning("MayaVC: no file open — cannot save w/ commit")
            return

        cur_base, cur_ext, cur_ver = _parse_ver(os.path.basename(cur))
        if cur_ver <= 0:
            cmds.warning("MayaVC: current file has no version — use incremental save first")
            return

        tag = f"{cur_base}_v{cur_ver:03d}"

        # Multi-line amend dialog
        append_msg, ok = show_amend_dialog(tag, parent=win)
        if not ok:
            return

        if not append_msg.strip():
            cmds.warning("MayaVC: empty message — commit skipped")
            return

        # Save
        try:
            mel.eval("file -save -f")
        except Exception as e:
            cmds.warning(f"MayaVC: save failed - {e}")
            return

        # Commit
        if vc_amend_commit(state["scenes_dir"], cur, cur_ver, append_msg.strip()):
            cmds.warning(f"MayaVC: commit appended to {tag}")
            do_refresh()
        else:
            cmds.warning("MayaVC: commit failed")

    def on_load():
        rows = {idx.row() for idx in table.selectedIndexes()}
        if not rows:
            return
        row = min(rows)
        tag = _tag_for_row(row)
        if not tag:
            return
        if load_version(state["scenes_dir"], tag):
            do_refresh()

    def on_folder():
        """Open the scenes folder and select the file in Explorer."""
        sd = state["scenes_dir"]
        if not sd or not os.path.isdir(sd):
            return
        # Try to select the current row's file
        rows = {idx.row() for idx in table.selectedIndexes()}
        if rows:
            row = min(rows)
            tag = _tag_for_row(row)
            r = state.get("tag_map", {}).get(tag)
            if r and r.file:
                fpath = os.path.join(sd, r.file)
                if os.path.isfile(fpath):
                    # Windows: explorer /select, highlights the file
                    subprocess.run(["explorer", "/select,", os.path.normpath(fpath)])
                    return
        os.startfile(sd)

    def on_rename():
        """Rename the selected version's file on disk + update JSON."""
        import maya.cmds as cmds

        rows = {idx.row() for idx in table.selectedIndexes()}
        if not rows:
            return
        row = min(rows)
        tag = _tag_for_row(row)
        if not tag:
            return
        r = state.get("tag_map", {}).get(tag)
        if not r:
            return
        sd = state["scenes_dir"]
        old_path = os.path.join(sd, r.file)
        if not os.path.isfile(old_path):
            cmds.warning(f"MayaVC: file not found — {r.file}")
            return

        # Prompt for new name (strip extension so user only edits the base)
        old_base, old_ext = os.path.splitext(r.file)
        result = cmds.promptDialog(
            title="Rename Version File",
            message=f"New name for {tag}:",
            text=old_base,
            button=["Rename", "Cancel"],
            defaultButton="Rename",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if result == "Cancel":
            return
        new_name = (cmds.promptDialog(q=True, text=True) or "").strip()
        if not new_name or new_name == old_base:
            return

        # Append original extension
        new_name = new_name + old_ext

        new_path = os.path.join(sd, new_name)
        if os.path.exists(new_path):
            cmds.warning(f"MayaVC: {new_name} already exists — rename cancelled")
            return

        try:
            os.rename(old_path, new_path)
        except Exception as e:
            cmds.warning(f"MayaVC: rename failed - {e}")
            return

        # Update JSON
        from core.vc_engine import _read_versions, _acquire_and_read, _write_versions_atomic, _unlock_file
        lock, data = _acquire_and_read(sd)
        if lock is not None:
            try:
                if tag in data:
                    data[tag]["file"] = new_name
                    _write_versions_atomic(sd, data)
            finally:
                _unlock_file(lock)

        cmds.warning(f"MayaVC: renamed {r.file} → {new_name}")
        do_refresh()

    def on_edit_desc():
        """Edit the description of the selected version."""
        import maya.cmds as cmds

        rows = {idx.row() for idx in table.selectedIndexes()}
        if not rows:
            return
        row = min(rows)
        tag = _tag_for_row(row)
        if not tag:
            return
        r = state.get("tag_map", {}).get(tag)
        if not r:
            return

        # Show full text including timestamps (read-only visually, but editable)
        # On save, timestamps are preserved from original positions
        ts_re = re.compile(r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\s*')
        # Detect multi-commit: \x1e separator (new) or multiple timestamp lines (legacy)
        raw = r.message or ""
        if "\x1e" in raw:
            old_messages = raw.split("\x1e")
        else:
            ts_count = sum(1 for l in raw.split("\n") if ts_re.match(l.strip()))
            if ts_count > 1:
                # Legacy multi-commit: group by timestamp
                commits, cur = [], []
                for l in raw.split("\n"):
                    if ts_re.match(l.strip()):
                        if cur: commits.append("\n".join(cur))
                        cur = [l]
                    else:
                        cur.append(l)
                if cur: commits.append("\n".join(cur))
                old_messages = commits
            else:
                old_messages = [raw]

        dlg = QDialog(win)
        dlg.setWindowTitle(f"Edit Description — {tag}")
        dlg.setMinimumSize(650, 400)
        dlg.resize(750, 500)
        dlg.setModal(True)
        dl = QVBoxLayout(dlg)

        # Use a table: Timestamp (read-only) | Body (editable)
        tbl = QTableWidget(0, 2)
        tbl.setHorizontalHeaderLabels(["Time", "Description"])
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        tbl.setColumnWidth(0, 150)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setWordWrap(True)
        tbl.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked | QAbstractItemView.AnyKeyPressed)

        # Use multi-line text editor for Description column
        class MultiLineDelegate(QStyledItemDelegate):
            def createEditor(self, parent, option, index):
                te = QTextEdit(parent)
                te.setAcceptRichText(False)
                return te
            def setEditorData(self, editor, index):
                editor.setPlainText(index.data(Qt.DisplayRole) or "")
            def setModelData(self, editor, model, index):
                model.setData(index, editor.toPlainText())
        tbl.setItemDelegateForColumn(1, MultiLineDelegate(tbl))

        for msg in old_messages:
            m = ts_re.match(msg)
            ts = m.group(0) if m else ""
            body = msg[m.end():] if m else msg

            tbl.insertRow(tbl.rowCount())
            ts_item = QTableWidgetItem(ts.strip())
            ts_item.setFlags(ts_item.flags() & ~Qt.ItemIsEditable)
            ts_item.setForeground(QColor("#999"))
            tbl.setItem(tbl.rowCount() - 1, 0, ts_item)

            body_item = QTableWidgetItem(body.strip())
            tbl.setItem(tbl.rowCount() - 1, 1, body_item)

        # Empty row for new entry
        tbl.insertRow(tbl.rowCount())
        ts_item = QTableWidgetItem(datetime.datetime.now().strftime("[%Y-%m-%d %H:%M]"))
        ts_item.setFlags(ts_item.flags() & ~Qt.ItemIsEditable)
        ts_item.setForeground(QColor("#999"))
        tbl.setItem(tbl.rowCount() - 1, 0, ts_item)
        tbl.setItem(tbl.rowCount() - 1, 1, QTableWidgetItem(""))

        dl.addWidget(tbl)

        # Auto-size rows for long text
        for i in range(tbl.rowCount()):
            tbl.resizeRowToContents(i)
            tbl.setRowHeight(i, max(tbl.rowHeight(i), 60))

        br = QHBoxLayout()
        br.addStretch()
        sb = QPushButton("Save")
        sb.setDefault(True)
        sb.clicked.connect(dlg.accept)
        br.addWidget(sb)
        cb = QPushButton("Cancel")
        cb.clicked.connect(dlg.reject)
        br.addWidget(cb)
        dl.addLayout(br)

        if dlg.exec() != QDialog.Accepted:
            return

        # Collect messages from table
        new_messages = []
        for i in range(tbl.rowCount()):
            ts = tbl.item(i, 0).text().strip() if tbl.item(i, 0) else ""
            body = tbl.item(i, 1).text().strip() if tbl.item(i, 1) else ""
            if body:
                new_messages.append(f"{ts} {body}")

        if new_messages == [m.strip() for m in old_messages if m.strip()]:
            return

        # Update JSON
        sd = state["scenes_dir"]
        from core.vc_engine import _acquire_and_read, _write_versions_atomic, _unlock_file
        lock, data = _acquire_and_read(sd)
        if lock is not None:
            try:
                if tag in data:
                    data[tag]["messages"] = new_messages
                    _write_versions_atomic(sd, data)
            finally:
                _unlock_file(lock)

        cmds.warning(f"MayaVC: description updated for {tag}")
        do_refresh()

    def on_delete():
        rows = {idx.row() for idx in table.selectedIndexes()}
        if not rows:
            return
        row = min(rows)
        tag = _tag_for_row(row)
        if not tag:
            return
        r = state.get("tag_map", {}).get(tag)
        if not r:
            return
        import maya.cmds as cmds
        import random
        code = str(random.randint(100, 999))
        result = cmds.promptDialog(
            title="⚠  DELETE VERSION — IRREVERSIBLE",
            message=(
                f"Permanently delete this version?\n\n"
                f"  Tag:    {r.tag}\n"
                f"  File:   {r.file}\n"
                f"  Date:   {r.date}\n\n"
                f"⚠ This will DELETE the file from disk.\n"
                f"   Type {code} to confirm:"
            ),
            text="",
            button=["Confirm", "Cancel"],
            defaultButton="Cancel",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if result != "Confirm":
            return
        entered = cmds.promptDialog(q=True, text=True) or ""
        if entered.strip() != code:
            cmds.warning(f"MayaVC: delete cancelled (code mismatch)")
            return
        if delete_version(state["scenes_dir"], r.tag, r.file):
            do_refresh()
            if state.get("edit_mode"):
                on_edit()

    def on_inc_save():
        """Incremental save + commit dialog + commit. Same as shelf_main."""
        import maya.cmds as cmds

        d = state["scenes_dir"]
        if not d or not os.path.isdir(d):
            cmds.warning("MayaVC: Cannot determine scenes directory.")
            return

        # 1. Preview next version (does NOT save / create anything yet)
        base, ext, next_ver, _ = dry_run_next_version(d)

        # 2. Commit dialog with editable base name + live version preview
        parent = win  # parent to Maya main window via our existing win widget

        new_base, new_ver, msg, ok = show_commit_dialog(
            base, next_ver, ext, parent=parent, scenes_dir=d,
        )
        if not ok:
            cmds.warning("MayaVC: Commit cancelled — nothing saved.")
            return

        # 3. Save-as to new versioned file
        new_path = os.path.join(d, f"{new_base}_v{new_ver:03d}.{ext}")
        ft = "mayaAscii" if ext == "ma" else "mayaBinary"
        try:
            cmds.file(rename=new_path)
            cmds.file(save=True, type=ft)
        except Exception as e:
            cmds.warning(f"MayaVC: save failed - {e}")
            return

        # 4. Record commit
        if vc_commit(d, new_path, new_ver, msg):
            cmds.warning(f"MayaVC: v{new_ver:03d} committed - {msg}")
            do_refresh()
        else:
            cmds.warning("MayaVC: Commit failed.")

    def on_clear_cache():
        """Clear Maya's module cache and reload MayaVC modules."""
        import sys
        import maya.cmds as cmds

        # Clear MayaVC modules from sys.modules
        to_remove = [k for k in sys.modules if k.startswith(("core.", "ui."))]
        for k in to_remove:
            del sys.modules[k]
        # Also remove top-level entries
        for top in ("core", "ui"):
            if top in sys.modules:
                del sys.modules[top]

        cmds.warning(f"MayaVC: cleared {len(to_remove)} cached modules — "
                     "reopen panel to reload.")

    def on_perf():
        """Open the performance monitor panel."""
        show_perf_panel()

    def on_toggle():
        if state["filter_mode"] == "all":
            filter_all_btn.setStyleSheet(dim_style)
            filter_cur_btn.setStyleSheet(active_style)
            do_refresh(filter_mode="current")
        else:
            filter_all_btn.setStyleSheet(active_style)
            filter_cur_btn.setStyleSheet(dim_style)
            do_refresh(filter_mode="all")

    def on_latest_only():
        if state["latest_only"]:
            latest_all_btn.setStyleSheet(active_style)
            latest_new_btn.setStyleSheet(dim_style)
            do_refresh(latest_only=False)
        else:
            latest_all_btn.setStyleSheet(dim_style)
            latest_new_btn.setStyleSheet(active_style)
            do_refresh(latest_only=True)

    def on_collapse_all():
        """Toggle collapse/expand all multi-commit messages."""
        visible = state.get("visible", [])
        # A message is expandable if it has embedded newlines (multi-line
        # body) or \x1e record separators (amends / multiple commits).
        def _is_expandable(msg):
            return "\n" in msg or "\x1e" in msg

        any_collapsed = any(
            _is_expandable(r.message or "") and _COLLAPSED_STATE.get(r.tag, True)
            for r in visible
        )
        new_state = not any_collapsed
        for r in visible:
            full_msg = r.message or ""
            if _is_expandable(full_msg):
                _COLLAPSED_STATE[r.tag] = new_state
        _save_collapsed()
        collapse_all_btn.setText("全部展开" if new_state else "全部收起")
        _rewrap_all_messages()

    refresh_btn.clicked.connect(do_refresh)
    edit_btn.clicked.connect(on_edit)
    delete_selected_btn.clicked.connect(on_delete_selected)
    clear_desc_btn.clicked.connect(on_clear_descriptions)
    collapse_all_btn.clicked.connect(on_collapse_all)
    filter_all_btn.clicked.connect(on_toggle)
    filter_cur_btn.clicked.connect(on_toggle)
    latest_all_btn.clicked.connect(on_latest_only)
    latest_new_btn.clicked.connect(on_latest_only)
    info_label.linkActivated.connect(lambda: _scroll_to_current())
    hdr.sectionResized.connect(_on_msg_col_resized)
    hdr.sectionClicked.connect(_on_section_clicked)
    table.itemSelectionChanged.connect(on_sel)
    delete_btn.clicked.connect(on_delete)
    save_commit_btn.clicked.connect(on_save_commit)
    inc_save_btn.clicked.connect(on_inc_save)

    # ---- right-click context menu on table ----
    table.setContextMenuPolicy(Qt.CustomContextMenu)
    def on_context_menu(pos):
        row = table.rowAt(pos.y())
        if row < 0 or row >= table.rowCount():
            return
        # Select the row under cursor
        table.selectRow(row)
        menu = QMenu(table)
        if state.get("edit_mode"):
            menu.addAction("Delete This Version", on_delete)
            menu.addSeparator()
            menu.addAction("Show in Folder", on_folder)
        else:
            menu.addAction("Open", on_load)
            menu.addAction("Rename", on_rename)
            menu.addAction("Edit Description", on_edit_desc)
            menu.addSeparator()
            menu.addAction("Show in Folder", on_folder)
        menu.exec_(table.viewport().mapToGlobal(pos))
    table.customContextMenuRequested.connect(on_context_menu)

    # ---- "?" help button menu (Clear Cache + Perf) ----
    help_menu = QMenu(help_btn)
    help_menu.addAction("Clear Cache", on_clear_cache)
    help_menu.addAction("Performance", on_perf)
    help_btn.setMenu(help_menu)
    help_btn.setPopupMode(QToolButton.InstantPopup)

    # Connect folder button now that _on_browse_project is defined
    folder_btn.clicked.connect(_on_browse_project)

    do_refresh()

    # ---- Restore panel state from previous session ----
    sv = state.get("_saved", {})
    # Sort indicator + header
    ls = state.get("_last_sort_section")
    if ls is not None and ls >= 0:
        dir_key = f"sort_dir_{ls}"
        order = (Qt.DescendingOrder if state.get(dir_key, False)
                 else Qt.AscendingOrder)
        table.horizontalHeader().setSortIndicator(ls, order)
        _update_sort_header(ls, order)
    # Segment button styles
    if state.get("latest_only"):
        latest_all_btn.setStyleSheet(dim_style)
        latest_new_btn.setStyleSheet(active_style)
    if state.get("filter_mode") == "current":
        filter_all_btn.setStyleSheet(dim_style)
        filter_cur_btn.setStyleSheet(active_style)
    # Scroll position
    if "_scroll" in sv:
        QTimer.singleShot(10, lambda v=sv["_scroll"]:
                          table.verticalScrollBar().setValue(v))
    # Selected row
    if sv.get("_sel_tag"):
        sel_tag = sv["_sel_tag"]
        for i in range(table.rowCount()):
            tag_item = table.item(i, 0)
            if tag_item and (tag_item.data(Qt.UserRole) or tag_item.text()) == sel_tag:
                table.selectRow(i)
                table.scrollToItem(table.item(i, 0), QTableWidget.PositionAtCenter)
                break
    # Save panel state on close
    win._mayavc_state = state
    win._mayavc_table = table
    win.destroyed.connect(lambda _win=win: _collect_and_save_from_window(_win))

    # stash reference so gc doesn't eat the window
    if not hasattr(show, "_windows"):
        show._windows = []
    show._windows.append(win)

    win.show()
    import maya.cmds as _dbg
    _dbg.warning("MayaVC: History window shown.")


# ---- Panel State Persistence (JSON file) ----

def _panel_state_path():
    """Path to the JSON file for panel state persistence."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "MayaVC_panel_state.json")


def _load_panel_state():
    """Load panel state from JSON file."""
    path = _panel_state_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_panel_state(state_dict):
    """Save panel state to JSON file."""
    path = _panel_state_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[MayaVC] Failed to save panel state: {e}")
