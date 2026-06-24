"""
core/gitignore.py - .gitignore template writer
"""

GITIGNORE_TEMPLATE = """\
# === Maya Version Control - Auto-generated ===
test/

# Maya temp files
*.tmp
*~
*.bak
*.swp

# Maya crash recovery
*.crash
*.before_crash*
*.after_crash*

# Autosave
autosave/
incrementalSave/
backup/

# OS files
.DS_Store
Thumbs.db
Desktop.ini

# Maya cache
*.mayaCache
*.mayaSwatches
*.mayaUsdViewCache
"""


def write_gitignore(directory: str) -> str:
    """
    Write .gitignore template if it does not exist.
    Returns the full path to .gitignore.
    """
    import os
    path = os.path.join(directory, ".gitignore")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(GITIGNORE_TEMPLATE)
    return path
