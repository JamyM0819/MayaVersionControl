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

from core.vc_engine import get_scenes_dir, get_history, load_version, _parse_ver


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


def _is_alive(widget):
    """Return True if the C++ object backing `widget` still exists."""
    try:
        from shiboken6 import isValid
        return isValid(widget)
    except Exception:
        pass
    # Fallback: try touching a property — if it raises, the C++ object is gone
    try:
        _ = widget.objectName()
        return True
    except Exception:
        return False


def _remove_from_stash(win):
    """Remove a destroyed window from the stash so gc can clean it up."""
    if not hasattr(show, "_windows"):
        return
    try:
        show._windows.remove(win)
    except ValueError:
        pass


def show():
    """Show the history browser. Reuses existing window if open."""
    # Check if window is still alive (not destroyed by Maya)
    mw = _get_maya_window()
    if mw is None:
        return

    # Clean up dead wrappers from the stash first
    if hasattr(show, "_windows"):
        show._windows = [w for w in show._windows if _is_alive(w)]

    # Reuse an existing living window
    if hasattr(show, "_windows"):
        for w in show._windows:
            try:
                if w.objectName() == "MayaVCHistoryWidget":
                    w.show()
                    w.raise_()
                    w.activateWindow()
                    return
            except Exception:
                continue

    win = QWidget(mw, Qt.Window)
    win.setObjectName("MayaVCHistoryWidget")
    win.setAttribute(Qt.WA_DeleteOnClose)      # destroy C++ object on close
    win.setWindowTitle("Maya Version History")
    win.resize(900, 550)
    win.setMinimumSize(600, 350)
    # On close, remove from stash so we don't hold a dangling wrapper
    win.destroyed.connect(lambda obj=None: _remove_from_stash(win))

    lay = QVBoxLayout(win)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.setSpacing(4)

    label = QLabel("Project: (click Refresh)")
    lay.addWidget(label)

    table = QTableWidget(0, 3)
    table.setHorizontalHeaderLabels(["Version", "Date", "Message"])
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    h = table.horizontalHeader()
    h.setStretchLastSection(True)
    h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
    h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
    h.setSectionResizeMode(2, QHeaderView.Stretch)
    lay.addWidget(table, stretch=1)

    blay = QHBoxLayout()
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

    # ---- state ----
    state = {"records": [], "scenes_dir": ""}

    def do_refresh():
        state["records"] = []
        state["scenes_dir"] = ""
        table.setRowCount(0)
        label.setText("Loading...")
        try:
            d = get_scenes_dir()
        except Exception:
            d = os.getcwd()
        state["scenes_dir"] = d

        # Determine current scene's base name for filtering
        scene_name = None
        try:
            import maya.cmds as cmds
            p = cmds.file(q=True, sn=True)
            if p:
                scene_name = _parse_ver(p)[0]
        except Exception:
            pass

        try:
            state["records"] = get_history(d, scene_name)
            suffix = f" for '{scene_name}'" if scene_name else ""
            label.setText(
                f"Project: {os.path.basename(d) or d}{suffix}"
                f"  ({len(state['records'])} versions)"
            )
        except Exception as e:
            label.setText(f"Error: {e}")

        table.setRowCount(len(state["records"]))
        for i, r in enumerate(state["records"]):
            tag = QTableWidgetItem(r.tag or "-")
            tag.setTextAlignment(Qt.AlignCenter)
            table.setItem(i, 0, tag)
            table.setItem(i, 1, QTableWidgetItem(r.date or ""))
            table.setItem(i, 2, QTableWidgetItem(r.message or ""))

    def on_sel():
        rows = {idx.row() for idx in table.selectedIndexes()}
        en = bool(rows) and min(rows) < len(state["records"])
        load_btn.setEnabled(en)
        folder_btn.setEnabled(en)

    def on_load():
        rows = {idx.row() for idx in table.selectedIndexes()}
        if not rows:
            return
        r = state["records"][min(rows)]
        load_version(state["scenes_dir"], r.tag)

    def on_folder():
        d = state["scenes_dir"]
        if d and os.path.isdir(d):
            os.startfile(d)

    refresh_btn.clicked.connect(do_refresh)
    table.itemSelectionChanged.connect(on_sel)
    load_btn.clicked.connect(on_load)
    folder_btn.clicked.connect(on_folder)

    do_refresh()
    # stash reference so gc doesn't eat the window
    if not hasattr(show, "_windows"):
        show._windows = []
    show._windows.append(win)

    win.show()
    print("MayaVC: History window shown.")
