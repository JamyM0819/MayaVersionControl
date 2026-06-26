"""
ui/history_browser.py - Version history browser.
Simple QWidget with Qt.Window flag, parented to Maya main window.
"""

import os
import re
import textwrap as _textwrap

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QAbstractItemView,
    QToolButton, QMenu, QLineEdit, QWidgetAction, QFileDialog,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from shiboken6 import isValid as _isValid

from core.vc_engine import (get_scenes_dir, get_history, load_version, delete_version,
                              _parse_ver, get_plugin_repo_hash, vc_amend_commit,
                              dry_run_next_version, vc_commit)
from core.perf_monitor import show_perf_panel
from ui.commit_dialog import show_commit_dialog, show_amend_dialog


# ---------------------------------------------------------------------------
# Persistent collapsed-state storage (survives Maya sessions)
# ---------------------------------------------------------------------------
_COLLAPSED_STATE = {}  # tag -> True/False, in-memory; write to Maya optionVar


def _load_collapsed():
    """Load collapsed state from Maya optionVars."""
    global _COLLAPSED_STATE
    try:
        import maya.cmds as cmds
        raw = cmds.optionVar(q="MayaVC_collapsed") or ""
        for item in raw.split(";;"):
            if "=" in item:
                k, v = item.split("=", 1)
                _COLLAPSED_STATE[k] = v == "1"
    except Exception:
        pass


def _save_collapsed():
    """Persist collapsed state to Maya optionVars."""
    try:
        import maya.cmds as cmds
        parts = [f"{k}={1 if v else 0}" for k, v in _COLLAPSED_STATE.items()]
        cmds.optionVar(sv=("MayaVC_collapsed", ";;".join(parts)))
    except Exception:
        pass


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


def show():
    """Show the history browser. Always creates a fresh window."""
    _load_collapsed()

    mw = _get_maya_window()
    if mw is None:
        return

    # Close any existing history window first — save geometry before closing
    if hasattr(show, "_windows"):
        for w in show._windows[:]:
            try:
                if _isValid(w):
                    # Save position/size before closing (use plain ints, not saveGeometry)
                    pos = w.pos()
                    sz = w.size()
                    _save_geometry(pos.x(), pos.y(), sz.width(), sz.height())
                    _save_collapsed()
                    w.close()
            except Exception:
                pass
        show._windows.clear()

    # Scenes dir — try cached first, then detect from Maya
    d = getattr(show, "_last_scenes_dir", "") or ""
    if not d or not os.path.isdir(d):
        try:
            d = get_scenes_dir()
        except Exception:
            d = os.getcwd()
    show._last_scenes_dir = d

    # Repo hash for title — use plugin's own hash, not Maya project's
    repo_hash = get_plugin_repo_hash() or ""
    if repo_hash:
        repo_hash = f"  [{repo_hash}]"

    # ---- window ----
    win = QWidget(mw, Qt.Window)
    win.setObjectName("MayaVCHistoryWidget")
    win.setWindowTitle(f"Maya Version History{repo_hash}")

    # Restore previous geometry if available
    geo = _load_geometry()
    if geo:
        x, y, w, h = geo
        win.setGeometry(x, y, w, h)
    else:
        win.resize(900, 550)

    win.setMinimumSize(600, 350)

    lay = QVBoxLayout(win)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.setSpacing(4)

    top_bar = QHBoxLayout()

    # ---- Set Project (Maya-style: label + dropdown + folder icon) ----
    proj_wrapper = QVBoxLayout()
    proj_wrapper.setSpacing(1)
    proj_label = QLabel("Set Project")
    proj_label.setStyleSheet("font-size: 10px; color: #999; padding-left: 2px;")
    proj_wrapper.addWidget(proj_label)

    set_project_btn = QToolButton()
    set_project_btn.setPopupMode(QToolButton.MenuButtonPopup)
    set_project_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    set_project_btn.setMinimumWidth(160)
    set_project_menu = QMenu(set_project_btn)
    set_project_btn.setMenu(set_project_menu)

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
        # Build list of (label, path) — current path always first
        items = []
        if current_dir and os.path.isdir(current_dir):
            items.append((os.path.basename(current_dir) or current_dir, current_dir))
        for p in recent:
            p = os.path.normpath(p)
            if p and os.path.isdir(p) and p != current_dir:
                items.append((os.path.basename(p) or p, p))
        items = list(dict.fromkeys(items))  # dedup keeping order

        # Filter box narrows the menu items
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

        # -- Bottom actions --
        browse_a = set_project_menu.addAction("📂  Browse...")
        browse_a.triggered.connect(_on_browse_project)
        snap_a = set_project_menu.addAction("📍  Use Current Maya Project")
        snap_a.triggered.connect(_on_use_current_maya_project)

    set_project_menu.aboutToShow.connect(_build_project_menu)

    def _update_project_button_text():
        d = state.get("scenes_dir", "")
        if d and os.path.isdir(d):
            set_project_btn.setText(os.path.basename(d) or d)
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

    proj_wrapper.addWidget(set_project_btn)
    top_bar.addLayout(proj_wrapper)
    top_bar.addSpacing(8)

    label = QLabel("Project: (click Refresh)")
    top_bar.addWidget(label, stretch=1)
    collapse_all_btn = QPushButton("全部收起")
    top_bar.addWidget(collapse_all_btn)
    latest_only_btn = QPushButton("只看最新")
    top_bar.addWidget(latest_only_btn)
    filter_toggle_btn = QPushButton("只看当前")
    top_bar.addWidget(filter_toggle_btn)
    lay.addLayout(top_bar)

    info_label = QLabel("")
    info_label.setStyleSheet("font-size: 12px; font-weight: bold;")
    info_label.setTextFormat(Qt.RichText)
    info_label.setCursor(Qt.PointingHandCursor)
    lay.addWidget(info_label)

    table = QTableWidget(0, 3)
    table.setHorizontalHeaderLabels(["Version", "Date", "Message"])
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(False)  # we manage row colours ourselves
    table.verticalHeader().setVisible(False)
    hdr = table.horizontalHeader()
    hdr.setSectionResizeMode(0, QHeaderView.Interactive)
    hdr.setSectionResizeMode(1, QHeaderView.Interactive)
    hdr.setSectionResizeMode(2, QHeaderView.Stretch)  # fill remaining width; resize triggers rewrap
    # Default column widths
    table.setColumnWidth(0, 150)
    table.setColumnWidth(1, 140)
    table.setColumnWidth(2, 300)
    # Disable native word-wrap on Message column — we handle wrapping ourselves
    table.setWordWrap(False)
    lay.addWidget(table, stretch=1)

    # Enable click-to-sort on column headers
    table.setSortingEnabled(True)

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
    clear_cache_btn = QPushButton("Clear Cache")
    blay.addWidget(clear_cache_btn)
    perf_btn = QPushButton("Perf")
    blay.addWidget(perf_btn)
    delete_btn = QPushButton("Delete This Version")
    delete_btn.setEnabled(False)
    blay.addWidget(delete_btn)
    edit_btn = QPushButton("Edit")
    blay.addWidget(edit_btn)
    delete_selected_btn = QPushButton("Delete Selected")
    delete_selected_btn.setEnabled(False)
    delete_selected_btn.setVisible(False)
    blay.addWidget(delete_selected_btn)
    blay.addStretch()
    load_btn = QPushButton("Load This Version")
    load_btn.setEnabled(False)
    blay.addWidget(load_btn)
    folder_btn = QPushButton("Show in Folder")
    folder_btn.setEnabled(False)
    blay.addWidget(folder_btn)
    refresh_btn = QPushButton("Refresh")
    blay.addWidget(refresh_btn)
    lay.addLayout(blay)
    state = {"records": [], "scenes_dir": d, "filter_mode": "all",
             "latest_only": False, "edit_mode": False, "cur_tag": None}

    _update_project_button_text()

    def _visible_records(records, latest_only):
        """If latest_only, return only the highest-version record per base name."""
        if not latest_only:
            return records
        best = {}
        ver_re = re.compile(r'^(.+)_v(\d{3,})$')
        for r in records:
            m = ver_re.match(r.tag)
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
        if filter_mode is not None:
            state["filter_mode"] = filter_mode
        if latest_only is not None:
            state["latest_only"] = latest_only

        state["records"] = []
        # Temporarily disable sorting while we rebuild the table — then
        # re-enable it so Qt applies the current sort indicator on its own.
        table.setSortingEnabled(False)
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
                    if cur_ver > 0:
                        cur_tag = f"{cur_base}_v{cur_ver:03d}"
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
        cur_tag = state.get("cur_tag")
        cur_row = None

        # --- group colours ---
        # Called AFTER table items exist so _apply_group_colours can write
        # to actual cells.
        table.setRowCount(len(visible))
        for i, r in enumerate(visible):
            is_current = cur_tag and r.tag == cur_tag
            if is_current:
                cur_row = i

            tag_item = QTableWidgetItem(r.tag or "-")
            tag_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 0, tag_item)
            date_item = QTableWidgetItem(r.date or "")
            date_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 1, date_item)

            # Message column: fold multi-line, store full as UserRole
            full_msg = r.message or ""
            folded = _collapsed_for_tag(r.tag, full_msg)
            msg_item = QTableWidgetItem(folded)
            msg_item.setData(Qt.UserRole, full_msg)
            table.setItem(i, 2, msg_item)

            # Highlight current version row
            if is_current:
                for col in range(3):
                    cell = table.item(i, col)
                    cell.setBackground(QColor("#27AE60"))
                    cell.setForeground(QColor("#FFFFFF"))

            # Expand row height for expanded (▲) messages
            if "\n" in folded:
                table.resizeRowToContents(i)

        # --- group colours (deferred until Qt applies the sort indicator) ---
        QTimer.singleShot(0, lambda: _apply_group_colours(visible, cur_tag))

        # Re-enable sorting AFTER items are in place — this makes Qt apply the
        # current sort indicator to the fresh data without losing cell content.
        table.setSortingEnabled(True)

        # Auto-scroll to current version row
        _scroll_to_current()

        # Deferred rewrap: column width may not be final during initial layout.
        QTimer.singleShot(0, _rewrap_all_messages)

    def _msg_col_chars():
        """Return chars that fit in the Message column at current width."""
        fm = table.fontMetrics()
        col_w = table.columnWidth(2)
        # Use widest CJK char width — Chinese chars are ~2x as wide as Latin
        cjk_w = fm.horizontalAdvance("█")
        if cjk_w <= 0:
            cjk_w = fm.averageCharWidth() or 7
        pad = 12
        avail = max(10, col_w - pad)
        return max(15, int(avail / cjk_w))

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
        """Build the display text for a message cell.

        Every record starts with a timestamp line, followed by its body
        indented beneath.  This guarantees timestamp and body text never
        overlap vertically — the timestamp is always on its own short line.
        """
        if "\n" not in full_msg:
            # Single commit — no arrow, timestamp on own line, body wrapped below
            ts, body = _split_ts_body(full_msg)
            if ts:
                body_text = _wrap_body(body, wrap_chars, "   ")
                if body_text.strip():
                    return ts.rstrip() + "\n" + body_text
                return ts + body
            return _wrap_body(full_msg, wrap_chars, "")

        # Multi-commit (vc_amend_commit appends)
        records = full_msg.split("\n")
        blocks = []
        for rec in records:
            ts, body = _split_ts_body(rec)
            if collapsed:
                # Only show first record, single-line summary
                arrow = "▼ "
                if ts:
                    body_text = _wrap_body(body, wrap_chars, "      ")
                    if body_text.strip():
                        blocks.append(arrow + ts.rstrip() + "\n" + body_text)
                    else:
                        blocks.append(arrow + ts + body)
                else:
                    blocks.append(arrow + _wrap_body(rec, wrap_chars, "      "))
                break  # one record in collapsed mode
            else:
                # Expanded: each record gets its own ts line + body block
                arrow = "▲ " if not blocks else "   "  # ▲ on first, rest plain
                if ts:
                    body_text = _wrap_body(body, wrap_chars, "      ")
                    blocks.append(arrow + ts.rstrip() + "\n" + body_text)
                else:
                    blocks.append(arrow + _wrap_body(rec, wrap_chars, "      "))
        return "\n".join(blocks)

    def _apply_group_colours(visible, cur_tag, sort_section=-1):
        """Paint alternating backgrounds per base-name group when sorted by
        Version (section 0).  Disable group colours on Date/Message sort."""

        if not visible:
            return
        ver_re = re.compile(r'^(.+)_v(\d{3,})$')

        # ------ decide whether group colours should be ON ------
        if sort_section < 0:
            sort_section = table.horizontalHeader().sortIndicatorSection()
        row_count = table.rowCount()
        if row_count == 0:
            return

        if sort_section == 0:
            # Walk table rows top-to-bottom and collect the *first* occurrence
            # of each base name — this gives us a stable bases_in_order sequence
            # regardless of ascending/descending sort direction.
            seen = set()
            bases_in_order = []
            for i in range(row_count):
                tag_item = table.item(i, 0)
                tag = tag_item.text() if tag_item else ""
                m = ver_re.match(tag)
                base = m.group(1) if m else tag
                if base not in seen:
                    seen.add(base)
                    bases_in_order.append(base)
            base_order = {b: idx for idx, b in enumerate(bases_in_order)}

            for i in range(row_count):
                tag_item = table.item(i, 0)
                tag = tag_item.text() if tag_item else ""
                if cur_tag and tag == cur_tag:
                    continue
                m = ver_re.match(tag)
                base = m.group(1) if m else tag
                c = _GROUP_COLOURS[base_order.get(base, 0) % len(_GROUP_COLOURS)]
                for col in range(3):
                    cell = table.item(i, col)
                    if cell:
                        cell.setBackground(c)
        else:
            # Date (1) or Message (2) sort — clear all non-current backgrounds
            for i in range(row_count):
                tag_item = table.item(i, 0)
                tag = tag_item.text() if tag_item else ""
                if cur_tag and tag == cur_tag:
                    continue
                for col in range(3):
                    cell = table.item(i, col)
                    if cell:
                        cell.setBackground(QColor(255, 255, 255, 0))  # transparent

    def _rewrap_all_messages():
        """Re-wrap all visible message texts for current column width."""
        visible = state.get("visible")
        if not visible:
            return
        w = _msg_col_chars()
        for i, r in enumerate(visible):
            full_msg = r.message or ""
            new_text = _collapsed_for_tag(r.tag, full_msg, wrap_chars=w)
            item = table.item(i, 2)
            if item and item.text() != new_text:
                item.setText(new_text)
                table.resizeRowToContents(i)

    def _on_msg_col_resized(col, old_w, new_w):
        if col == 2 and new_w != old_w:
            _rewrap_all_messages()

    def _on_sort_changed(section, order):
        """When the user sorts by Date (1) or Message (2), clear group colours.
        When they sort back to Version (0), re-apply them after Qt has
        reshuffled the table rows."""
        visible = state.get("visible")
        cur_tag = state.get("cur_tag")
        if visible:
            # Qt sorts asynchronously — defer so table rows are in final order
            QTimer.singleShot(0, lambda: _apply_group_colours(visible, cur_tag, sort_section=section))

    def _collapsed_for_tag(tag, full_msg, wrap_chars=None):
        """Return the display text for message column, respecting collapse state."""
        if wrap_chars is None:
            wrap_chars = _msg_col_chars()
        collapsed = _COLLAPSED_STATE.get(tag, True)
        return _build_msg_display(full_msg, collapsed, wrap_chars)

    def _scroll_to_current():
        """Scroll to and select the current version row, if visible."""
        cur_tag = state.get("cur_tag")
        if not cur_tag:
            return
        visible = state.get("visible", [])
        for i, r in enumerate(visible):
            if r.tag == cur_tag:
                table.selectRow(i)
                table.scrollToItem(table.item(i, 0), QTableWidget.PositionAtCenter)
                break

    def on_msg_click(item):
        """Toggle collapse/expand for message column on double-click."""
        if item.column() != 2:
            return
        r = state["visible"][item.row()]
        tag = r.tag
        full_msg = r.message or ""
        if "\n" not in full_msg:
            return

        # Toggle
        cur = _COLLAPSED_STATE.get(tag, True)
        _COLLAPSED_STATE[tag] = not cur
        _save_collapsed()

        # Update the item text immediately
        new_text = _collapsed_for_tag(tag, full_msg, wrap_chars=_msg_col_chars())
        msg_item = table.item(item.row(), 2)
        msg_item.setText(new_text)
        table.resizeRowToContents(item.row())

    table.itemClicked.connect(on_msg_click)

    def on_sel():
        if state["edit_mode"]:
            rows = {idx.row() for idx in table.selectedIndexes()}
            en = bool(rows) and min(rows) < len(state.get("visible", []))
            delete_selected_btn.setEnabled(en)
            delete_btn.setEnabled(True)
        else:
            rows = {idx.row() for idx in table.selectedIndexes()}
            en = bool(rows) and min(rows) < len(state.get("visible", []))
            load_btn.setEnabled(en)
            folder_btn.setEnabled(en)
            delete_btn.setEnabled(en)

    def on_edit():
        if state["edit_mode"]:
            # Exit edit mode
            edit_btn.setText("Edit")
            edit_btn.setStyleSheet("")
            delete_selected_btn.setVisible(False)
            load_btn.setVisible(True)
            folder_btn.setVisible(True)
            delete_btn.setVisible(True)
            table.setSelectionMode(QAbstractItemView.SingleSelection)
            state["edit_mode"] = False
        else:
            # Enter edit mode
            edit_btn.setText("Done")
            edit_btn.setStyleSheet("background-color: #e74c3c; color: #fff; font-weight: bold;")
            delete_selected_btn.setVisible(True)
            load_btn.setVisible(False)
            folder_btn.setVisible(False)
            delete_btn.setVisible(False)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            state["edit_mode"] = True

    def on_delete_selected():
        rows = sorted({idx.row() for idx in table.selectedIndexes()})
        if not rows:
            return
        visible = state["visible"]
        selected_tags = [visible[i].tag for i in rows if i < len(visible)]
        if not selected_tags:
            return

        tag_list = "\n".join(f"  {visible[i].tag}  ({visible[i].date})"
                             for i in rows if i < len(visible))
        import maya.cmds as cmds
        confirmed = cmds.confirmDialog(
            title="⚠  DELETE MULTIPLE VERSIONS — IRREVERSIBLE",
            message=(
                f"Permanently delete {len(selected_tags)} versions?\n\n"
                f"{tag_list}\n\n"
                f"⚠ {len(selected_tags)} files will be deleted from disk.\n"
                f"   There is NO undo for this operation."
            ),
            button=["Yes, Delete All", "Cancel"],
            defaultButton="Cancel",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if confirmed == "Yes, Delete All":
            failed = 0
            for i in rows:
                if i < len(visible):
                    r = visible[i]
                    if not delete_version(state["scenes_dir"], r.tag, r.file):
                        failed += 1
            if failed:
                cmds.warning(f"MayaVC: {failed} of {len(rows)} deletions failed")
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
        r = state["visible"][min(rows)]
        if load_version(state["scenes_dir"], r.tag):
            do_refresh()

    def on_folder():
        sd = state["scenes_dir"]
        if sd and os.path.isdir(sd):
            os.startfile(sd)

    def on_delete():
        rows = {idx.row() for idx in table.selectedIndexes()}
        if not rows:
            return
        r = state["visible"][min(rows)]
        import maya.cmds as cmds
        confirmed = cmds.confirmDialog(
            title="⚠  DELETE VERSION — IRREVERSIBLE",
            message=(
                f"Permanently delete this version?\n\n"
                f"  Tag:    {r.tag}\n"
                f"  File:   {r.file}\n"
                f"  Date:   {r.date}\n\n"
                f"⚠ This will DELETE the original project file from disk.\n"
                f"   There is NO undo for this operation."
            ),
            button=["Yes, Delete It", "Cancel"],
            defaultButton="Cancel",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if confirmed == "Yes, Delete It":
            if delete_version(state["scenes_dir"], r.tag, r.file):
                do_refresh()

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
            filter_toggle_btn.setText("全部显示")
            do_refresh(filter_mode="current")
        else:
            filter_toggle_btn.setText("只看当前")
            do_refresh(filter_mode="all")

    def on_latest_only():
        if state["latest_only"]:
            latest_only_btn.setText("只看最新")
            do_refresh(latest_only=False)
        else:
            latest_only_btn.setText("历史版本")
            do_refresh(latest_only=True)

    def on_collapse_all():
        """Collapse all expanded multi-commit messages."""
        # Set all tags to collapsed
        visible = state.get("visible", [])
        for r in visible:
            full_msg = r.message or ""
            if "\n" in full_msg:
                _COLLAPSED_STATE[r.tag] = True
        _save_collapsed()
        # Just rewrap – do_refresh would also work but rewrap is lighter
        _rewrap_all_messages()

    refresh_btn.clicked.connect(do_refresh)
    edit_btn.clicked.connect(on_edit)
    delete_selected_btn.clicked.connect(on_delete_selected)
    collapse_all_btn.clicked.connect(on_collapse_all)
    filter_toggle_btn.clicked.connect(on_toggle)
    latest_only_btn.clicked.connect(on_latest_only)
    info_label.linkActivated.connect(lambda: _scroll_to_current())
    hdr.sectionResized.connect(_on_msg_col_resized)
    hdr.sortIndicatorChanged.connect(_on_sort_changed)
    table.itemSelectionChanged.connect(on_sel)
    load_btn.clicked.connect(on_load)
    folder_btn.clicked.connect(on_folder)
    delete_btn.clicked.connect(on_delete)
    save_commit_btn.clicked.connect(on_save_commit)
    inc_save_btn.clicked.connect(on_inc_save)
    clear_cache_btn.clicked.connect(on_clear_cache)
    perf_btn.clicked.connect(on_perf)

    do_refresh()

    # stash reference so gc doesn't eat the window
    if not hasattr(show, "_windows"):
        show._windows = []
    show._windows.append(win)

    win.show()
    import maya.cmds as _dbg
    _dbg.warning("MayaVC: History window shown.")
