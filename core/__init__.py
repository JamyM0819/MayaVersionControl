"""core/__init__.py"""
from core.vc_engine import (
    VersionRecord,
    get_scenes_dir,
    detect_next_version,
    incremental_save,
    git_commit,
    get_history,
    get_current_info,
    load_version,
    _parse_ver,
)
from core.gitignore import write_gitignore
