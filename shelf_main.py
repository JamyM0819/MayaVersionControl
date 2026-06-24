"""
shelf_main.py - Maya Shelf button entry point
"""

import os
import sys

import maya.cmds as cmds

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

from core.vc_engine import get_scenes_dir, dry_run_next_version, git_commit
from core.gitignore import write_gitignore
from ui.commit_dialog import show_commit_dialog


def incremental_save_and_commit():
    """Incremental save + commit dialog + git commit.

    Version is previewed in the dialog but only *locked in* when the user
    hits Commit — cancelling leaves no new file on disk.
    """
    scenes_dir = get_scenes_dir()
    if not scenes_dir or not os.path.isdir(scenes_dir):
        cmds.warning("MayaVC: Cannot determine scenes directory.")
        return

    # 1. Preview next version (does NOT save / create anything yet)
    base, ext, next_ver, _ = dry_run_next_version(scenes_dir)

    # 2. Commit dialog with editable base name + live version preview.
    #    Returns the final base name, version, message.
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

    new_base, new_ver, msg, ok = show_commit_dialog(
        base, next_ver, ext, parent=parent, scenes_dir=scenes_dir,
    )
    if not ok:
        cmds.warning("MayaVC: Commit cancelled — nothing saved.")
        return

    # 3. Save-as to new versioned file
    new_path = os.path.join(scenes_dir, f"{new_base}_v{new_ver:03d}.{ext}")
    ft = "mayaAscii" if ext == "ma" else "mayaBinary"
    try:
        cmds.file(rename=new_path)
        cmds.file(save=True, type=ft)
    except Exception as e:
        cmds.warning(f"MayaVC: save failed - {e}")
        return

    # 4. Git commit + tag
    if git_commit(scenes_dir, new_path, new_ver, msg):
        cmds.warning(f"MayaVC: v{new_ver:03d} committed - {msg}")
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
