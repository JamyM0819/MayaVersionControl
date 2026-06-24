
# -*- coding: utf-8 -*-
"""
userSetup.py — Maya auto-load entry for Maya Version Control plugin

Place this file in:
  Windows: %USERPROFILE%\\Documents\\maya\\<version>\\scripts\\userSetup.py
  Mac:     ~/Library/Preferences/Autodesk/maya/<version>/scripts/userSetup.py
  Linux:   ~/maya/<version>/scripts/userSetup.py

Or run install.py to set it up automatically.
"""

import sys
import os

# Update to your actual path
_MVC_PATH = os.path.dirname(os.path.abspath(__file__)).replace(
    os.path.sep + "scripts", ""
)
# If you placed MayaVersionControl elsewhere, change this:
# _MVC_PATH = r"F:\path\to\MayaVersionControl"

if _MVC_PATH not in sys.path:
    sys.path.insert(0, _MVC_PATH)

print(f"MayaVC: Loaded - {_MVC_PATH}")
