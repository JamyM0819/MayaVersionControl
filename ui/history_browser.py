"""
ui/history_browser.py - Version history browser.
Simple QWidget with Qt.Window flag, parented to Maya main window.
"""

import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QAbstractItemView,
)
from PySide6.QtCore import Qt

from core.vc_engine import get_scenes_dir, get_history, load_version, delete_version, _parse_ver, _git, get_plugin_repo_hash


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
    state = {"records": [], "scenes_dir": d, "filter_mode": "all"}

    def do_refresh(filter_mode=None):
        if filter_mode is not None:
            state["filter_mode"] = filter_mode

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

        table.setRowCount(len(state["records"]))
        for i, r in enumerate(state["records"]):
            tag_item = QTableWidgetItem(r.tag or "-")
            tag_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 0, tag_item)
            hash_item = QTableWidgetItem(r.hash or "")
            hash_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 1, hash_item)
            table.setItem(i, 2, QTableWidgetItem(r.date or ""))
            table.setItem(i, 3, QTableWidgetItem(r.message or ""))

    def on_sel():
        rows = {idx.row() for idx in table.selectedIndexes()}
        en = bool(rows) and min(rows) < len(state["records"])
        load_btn.setEnabled(en)
        folder_btn.setEnabled(en)
        delete_btn.setEnabled(en)

    def on_load():
        rows = {idx.row() for idx in table.selectedIndexes()}
        if not rows:
            return
        r = state["records"][min(rows)]
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
        r = state["records"][min(rows)]
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
            # Switch to "current only" mode
            filter_toggle_btn.setText("全部显示")
            do_refresh("current")
        else:
            # Switch back to "show all" mode
            filter_toggle_btn.setText("只看当前")
            do_refresh("all")

    refresh_btn.clicked.connect(do_refresh)
    filter_toggle_btn.clicked.connect(on_toggle)
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
