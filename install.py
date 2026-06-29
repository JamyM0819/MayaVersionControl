"""
MayaVersionControl - One-click installer

Drag this file into Maya viewport to install.
Or run in Script Editor:
    exec(open(r"F:\\path\\to\\install.py", encoding="utf-8").read())
"""

import os, sys

# NOTE: If you see "does not contain drop function: onMayaDroppedPythonFile",
# Maya cached the old install module.  Clear it first in Script Editor:
#     import sys; sys.modules.pop('install', None)
# Then re-run, or use the exec() form below which bypasses caching.

# ── Detect plugin folder ──
_PACKAGE_DIR = None

# Try __file__ first (works when drag-dropped or run as a script)
try:
    _PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    pass

# If __file__ failed, ask user via file dialog
if not _PACKAGE_DIR:
    try:
        import maya.cmds as cmds
        result = cmds.fileDialog2(
            dialogStyle=1, fileMode=3,
            caption="Select the MayaVersionControl folder", okCaption="Install",
        )
        if result:
            _PACKAGE_DIR = os.path.normpath(result[0])
    except Exception:
        pass

if not _PACKAGE_DIR or not os.path.isfile(os.path.join(_PACKAGE_DIR, "shelf_main.py")):
    raise RuntimeError(
        "Cannot find MayaVersionControl folder.\n\n"
        "Drag install.py into Maya viewport to auto-detect,\n"
        "or run exec() from Script Editor and select the folder when prompted."
    )

import maya.cmds as cmds
import maya.mel as mel


# ── Step 1: userSetup.py ──
maya_ver = cmds.about(version=True)
prefs = os.environ.get("MAYA_APP_DIR", "")
if prefs:
    user_setup = os.path.join(prefs, maya_ver, "scripts", "userSetup.py")
else:
    home = os.path.expanduser("~")
    user_setup = os.path.join(home, "Documents", "maya", maya_ver, "scripts", "userSetup.py")
os.makedirs(os.path.dirname(user_setup), exist_ok=True)

block = f"""
# === MayaVC ===
import sys
if r"{_PACKAGE_DIR}" not in sys.path:
    sys.path.insert(0, r"{_PACKAGE_DIR}")
"""
if os.path.exists(user_setup):
    with open(user_setup, "r", encoding="utf-8") as f:
        if _PACKAGE_DIR not in f.read():
            with open(user_setup, "a", encoding="utf-8") as f:
                f.write(block)
else:
    with open(user_setup, "w", encoding="utf-8") as f:
        f.write(block)

# ── Step 2: Shelf button (install to currently active tab) ──
shelf_tab = None
try:
    # Use active tab
    shelf_top = mel.eval("$tmp=$gShelfTopLevel")
    shelf_tab = cmds.shelfTabLayout(shelf_top, query=True, selectTab=True)
except Exception:
    pass
if not shelf_tab:
    # Fallback: use first available tab
    tabs = cmds.lsUI(type="shelfTabLayout") or []
    shelf_tab = tabs[0] if tabs else None
if not shelf_tab:
    raise RuntimeError("No shelf tab found. Open Maya's shelf first.")

cmds.shelfButton(
    parent=shelf_tab,
    label="MayaVC",
    annotation="MayaVC - Version History Browser\nClick to open version history",
    image=os.path.join(_PACKAGE_DIR, "MayaVersionControl_icon.png"),
    command=f"""
import sys
sys.path.insert(0, r"{_PACKAGE_DIR}")
from shelf_main import show_history
show_history()
""",
    sourceType="python",
)
mel.eval("saveAllShelves $gShelfTopLevel")

print(f"\n{'='*60}")
print("  MayaVC installed successfully!")
print(f"  Folder: {_PACKAGE_DIR}")
print(f"{'='*60}\n")


def onMayaDroppedPythonFile(path):
    """Called by Maya's executeDroppedPythonFile after import.
    All work already done at module level; nothing extra needed."""
    pass
