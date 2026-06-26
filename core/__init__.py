"""core/__init__.py"""
from core.vc_engine import (
    VersionRecord,
    get_scenes_dir,
    detect_next_version,
    dry_run_next_version,
    incremental_save,
    vc_commit,
    vc_amend_commit,
    get_history,
    delete_version,
    load_version,
    _parse_ver,
    get_plugin_repo_hash,
    get_repo_status,
)
# Backward-compat aliases
git_commit = vc_commit
git_amend_commit = vc_amend_commit
from core.perf_monitor import perf_timed, perf_scope, get_perf, show_perf_panel
