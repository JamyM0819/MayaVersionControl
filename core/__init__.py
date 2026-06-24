"""core/__init__.py"""
from core.vc_engine import (
    VersionRecord,
    get_scenes_dir,
    detect_next_version,
    dry_run_next_version,
    incremental_save,
    git_commit,
    get_history,
    load_version,
    _parse_ver,
    _git,
    get_plugin_repo_hash,
)
from core.gitignore import write_gitignore
