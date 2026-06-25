"""
ui/history_browser.py - Version history browser.
Simple QWidget with Qt.Window flag, parented to Maya main window.
"""

import os
import re

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QAbstractItemView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from core.vc_engine import get_scenes_dir, get_history, load_version, delete_version, _parse_ver, _git, get_plugin_repo_hash


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

    # Close any existing history window first
    if hasattr(show, "_windows"):
        for w in show._windows[:]:
            try:
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
    win.resize(900, 550)
    win.setMinimumSize(600, 350)

    lay = QVBoxLayout(win)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.setSpacing(4)

    top_bar = QHBoxLayout()
    label = QLabel("Project: (click Refresh)")
    top_bar.addWidget(label, stretch=1)
    latest_only_btn = QPushButton("只看最新")
    top_bar.addWidget(latest_only_btn)
    filter_toggle_btn = QPushButton("只看当前")
    top_bar.addWidget(filter_toggle_btn)
    lay.addLayout(top_bar)

    info_label = QLabel("")
    info_label.setStyleSheet("font-size: 11px; color: #888;")
    lay.addWidget(info_label)

    table = QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(["Version", "Hash", "Date", "Message"])
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    hdr = table.horizontalHeader()
    hdr.setStretchLastSection(True)
    hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(3, QHeaderView.Stretch)
    lay.addWidget(table, stretch=1)

    # Enable click-to-sort on column headers
    table.setSortingEnabled(True)

    blay = QHBoxLayout()
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
    delete_btn = QPushButton("Delete This Version")
    delete_btn.setEnabled(False)
    blay.addWidget(delete_btn)
    refresh_btn = QPushButton("Refresh")
    blay.addWidget(refresh_btn)
    lay.addLayout(blay)

    # ---- state ----
    state = {"records": [], "scenes_dir": d, "filter_mode": "all",
             "latest_only": False, "edit_mode": False}

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

            # current version + hash from the currently open Maya file
            try:
                import maya.cmds as cmds
                p = cmds.file(q=True, sn=True)
                if p:
                    cur_base, cur_ext, cur_ver = _parse_ver(p)
                    if cur_ver > 0:
                        cur_tag = f"{cur_base}_v{cur_ver:03d}"
                        cur_hash = _git(["log", "-1", "--format=%h", cur_tag, "--"], cwd=d) or ""
                        info_label.setText(
                            f"Current: {cur_tag}  |  commit: {cur_hash or '---'}"
                        )
                    else:
                        info_label.setText("Current: (unsaved)")
            except Exception:
                info_label.setText("")
        except Exception as e:
            label.setText(f"Error: {e}")

        visible = _visible_records(state["records"], state["latest_only"])
        state["visible"] = visible
        table.setRowCount(len(visible))
        for i, r in enumerate(visible):
            tag_item = QTableWidgetItem(r.tag or "-")
            tag_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 0, tag_item)
            hash_item = QTableWidgetItem(r.hash or "")
            hash_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 1, hash_item)
            table.setItem(i, 2, QTableWidgetItem(r.date or ""))

            # Message column: fold multi-line, store full as UserRole
            full_msg = r.message or ""
            folded = _collapsed_for_tag(r.tag, full_msg)
            msg_item = QTableWidgetItem(folded)
            msg_item.setData(Qt.UserRole, full_msg)
            table.setItem(i, 3, msg_item)

    def _collapsed_for_tag(tag, full_msg):
        """Return the display text for message column, respecting collapse state."""
        if "\n" not in full_msg:
            return full_msg
        collapsed = _COLLAPSED_STATE.get(tag, True)  # default collapsed
        lines = full_msg.split("\n")
        if collapsed:
            return "▼ " + lines[0]
        else:
            # First line with ▲ arrow, rest indented
            parts = ["▲ " + lines[0]] + ["   " + ln for ln in lines[1:]]
            return "\n".join(parts)

    def on_msg_click(item):
        """Toggle collapse/expand for message column on double-click."""
        if item.column() != 3:
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
        new_text = _collapsed_for_tag(tag, full_msg)
        msg_item = table.item(item.row(), 3)
        msg_item.setText(new_text)
        table.resizeRowToContents(item.row())

    table.itemDoubleClicked.connect(on_msg_click)

    def on_sel():
        if state["edit_mode"]:
            rows = {idx.row() for idx in table.selectedIndexes()}
            en = bool(rows) and min(rows) < len(state.get("visible", []))
            delete_selected_btn.setEnabled(en)
            load_btn.setEnabled(True)
            folder_btn.setEnabled(True)
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
            button=["Cancel", "Yes, Delete All"],
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
            button=["Cancel", "Yes, Delete It"],
            defaultButton="Cancel",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if confirmed == "Yes, Delete It":
            if delete_version(state["scenes_dir"], r.tag, r.file):
                do_refresh()

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

    refresh_btn.clicked.connect(do_refresh)
    edit_btn.clicked.connect(on_edit)
    delete_selected_btn.clicked.connect(on_delete_selected)
    filter_toggle_btn.clicked.connect(on_toggle)
    latest_only_btn.clicked.connect(on_latest_only)
    table.itemSelectionChanged.connect(on_sel)
    load_btn.clicked.connect(on_load)
    folder_btn.clicked.connect(on_folder)
    delete_btn.clicked.connect(on_delete)

    do_refresh()
    # stash reference so gc doesn't eat the window
    if not hasattr(show, "_windows"):
        show._windows = []
    show._windows.append(win)

    win.show()
    import maya.cmds as _dbg
    _dbg.warning("MayaVC: History window shown.")
