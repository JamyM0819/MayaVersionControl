"""
shelf_main.py - Maya Shelf button entry point
"""

import os
import sys

import maya.cmds as cmds

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

from core.vc_engine import get_scenes_dir, dry_run_next_version, incremental_save, git_commit
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
    base, ext, next_ver, preview_path = dry_run_next_version(scenes_dir)

    # 2. Commit dialog (shows the preview version)
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

    preview_name = os.path.basename(preview_path)
    msg, ok = show_commit_dialog(preview_name, parent=parent)
    if not ok:
        cmds.warning("MayaVC: Commit cancelled — nothing saved.")
        return

    # 3. Commit confirmed — save-as new versioned file now.
    new_path = os.path.join(scenes_dir, f"{base}_v{next_ver:03d}.{ext}")
    ft = "mayaAscii" if ext == "ma" else "mayaBinary"
    # Save the current scene directly to the new versioned path (save-as),
    # without calling rename first. This preserves the user's original
    # working file untouched.
    try:
        cmds.file(rename=new_path)
        cmds.file(save=True, type=ft)
    except Exception as e:
        cmds.warning(f"MayaVC: save failed - {e}")
        return

    # 4. Git commit + tag
    if git_commit(scenes_dir, new_path, next_ver, msg):
        cmds.warning(f"MayaVC: v{next_ver:03d} committed - {msg}")
    else:
        cmds.warning("MayaVC: Commit failed.")
        # File was saved even if git failed — warn but don't lose work


def show_history():
    from ui.history_browser import show as _show
    _show()


def show_status():
    from ui.status_widget import StatusWidget
    StatusWidget.show()


if __name__ == "__main__":
    incremental_save_and_commit()
