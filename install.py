"""
install.py - Maya Version Control one-click installer

Run in Maya Script Editor (UTF-8 safe):
    exec(open(r"F:\\path\\to\\MayaVersionControl\\install.py", encoding="utf-8").read())

Automatically:
  1. Add plugin path to userSetup.py sys.path
  2. Add two buttons to current Maya shelf
"""

import os
import sys

# Hardcoded install path - change this if you move the plugin folder
_PACKAGE_DIR = r"F:\temp\jm\claude_project\MayaVersionControl"

import maya.cmds as cmds


def _get_user_setup_path() -> str:
    """Returns the path to userSetup.py in Maya user scripts directory."""
    maya_version = cmds.about(version=True)
    # Maya 2025+ userSetup.py location
    prefs = os.path.join(os.environ.get("MAYA_APP_DIR", ""), maya_version, "scripts")
    if not os.path.isdir(prefs):
        # try default location
        home = os.path.expanduser("~")
        prefs = os.path.join(home, "Documents", "maya", maya_version, "scripts")
    os.makedirs(prefs, exist_ok=True)
    return os.path.join(prefs, "userSetup.py")


def install_user_setup():
    """Append plugin path to userSetup.py so Maya finds it on startup."""
    user_setup = _get_user_setup_path()
    block = f"""
# === Maya Version Control Plugin ===
import sys
_mvc_path = r"{_PACKAGE_DIR}"
if _mvc_path not in sys.path:
    sys.path.insert(0, _mvc_path)
"""
    if os.path.exists(user_setup):
        with open(user_setup, "r", encoding="utf-8") as f:
            existing = f.read()
        if _PACKAGE_DIR in existing:
            print(f"MayaVC: userSetup.py already contains plugin path, skip.")
            return user_setup
        with open(user_setup, "a", encoding="utf-8") as f:
            f.write(block)
    else:
        with open(user_setup, "w", encoding="utf-8") as f:
            f.write(block)
    print(f"MayaVC: Updated userSetup.py -> {user_setup}")
    return user_setup


def install_shelf_buttons():
    """Add MayaVC buttons to the first available Maya shelf tab."""
    import maya.mel as mel

    # Dump all UI elements to find shelf tabs
    shelf_tabs = cmds.lsUI(type="shelfTabLayout") or []

    if not shelf_tabs:
        cmds.warning("MayaVC: No shelf tab found. Please open Maya first.")
        return

    shelf_tab = shelf_tabs[0]
    print(f"MayaVC: Using shelf tab = {shelf_tab}")

    cmds.shelfButton(
        parent=shelf_tab,
        label="VC Save",
        annotation="MayaVC - Incremental Save + Git Commit",
        image="menuIconSave.png",
        imageOverlayLabel="VC",
        command=f"""
import sys
sys.path.insert(0, r"{_PACKAGE_DIR}")
from shelf_main import incremental_save_and_commit
incremental_save_and_commit()
""",
        sourceType="python",
        width=35,
    )

    cmds.shelfButton(
        parent=shelf_tab,
        label="VC History",
        annotation="MayaVC - Version History Browser",
        image="menuIconSave.png",
        imageOverlayLabel="Hist",
        command=f"""
import sys
sys.path.insert(0, r"{_PACKAGE_DIR}")
from shelf_main import show_history
show_history()
""",
        sourceType="python",
        width=35,
    )

    # Save shelf so buttons persist across Maya restarts
    try:
        mel.eval("saveAllShelves $gShelfTopLevel")
    except Exception:
        pass

    print(f"MayaVC: Shelf buttons added to {shelf_tab}.")


def install():
    """One-click install"""
    print("=" * 60)
    print("  Maya Version Control Plugin - Install")
    print("=" * 60)
    install_user_setup()
    install_shelf_buttons()
    print()
    print("  Done! Find VC Save / VC History buttons on your Maya shelf.")
    print("=" * 60)


if __name__ == "__main__":
    install()
