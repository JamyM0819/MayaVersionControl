"""
shelf_main.py - Maya Shelf button entry point
"""

import os
import re
import sys

import maya.cmds as cmds

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

from core.vc_engine import get_scenes_dir, incremental_save, git_commit
from core.gitignore import write_gitignore
from ui.commit_dialog import show_commit_dialog


def incremental_save_and_commit():
    """Incremental save + commit dialog + git commit."""
    scenes_dir = get_scenes_dir()
    if not scenes_dir or not os.path.isdir(scenes_dir):
        cmds.warning("MayaVC: Cannot determine scenes directory.")
        return

    # 1. Save
    saved_path = incremental_save(scenes_dir)
    if saved_path is None:
        return

    # 2. Commit dialog
    parent = None
    try:
        from shiboken6 import wrapInstance
        import maya.OpenMayaUI as omui
        ptr = omui.MQtUtil.mainWindow()
        if ptr:
            from PySide6.QtWidgets import QWidget
            parent = wrapInstance(int(ptr), QWidget)
    except Exception:
        pass

    filename = os.path.basename(saved_path)
    msg, ok = show_commit_dialog(filename, parent=parent)
    if not ok:
        cmds.warning("MayaVC: Commit cancelled (file saved).")
        return

    # 3. Git commit + tag
    match = re.search(r'_v(\d{3,})\.', filename)
    version = int(match.group(1)) if match else 1

    if git_commit(scenes_dir, saved_path, version, msg):
        cmds.warning(f"MayaVC: v{version:03d} committed - {msg}")
    else:
        cmds.warning("MayaVC: Commit failed.")


def show_history():
    from ui.history_browser import show as _show
    _show()


def show_status():
    from ui.status_widget import StatusWidget
    StatusWidget.show()


if __name__ == "__main__":
    incremental_save_and_commit()
