"""
core/vc_engine.py - Maya Version Control core engine
Stores version metadata in .mayavc/versions.json (no git dependency).
"""

import datetime
import json
import os
import platform
import re
import subprocess

import maya.cmds as cmds
import maya.mel as mel

from core.perf_monitor import perf_timed, perf_scope


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

@perf_timed()
def get_scenes_dir():
    """Return the directory where scene files live.

    Falls back to workspace scenes/ when the current file is in a temp dir
    (e.g. after opening a historical version via MayaVC).
    """
    import tempfile
    path = cmds.file(q=True, sn=True)
    if path:
        d = os.path.dirname(os.path.abspath(path))
        if d and not _is_temp_dir(d):
            return d
    try:
        ws = cmds.workspace(q=True, rootDirectory=True)
        if ws:
            s = os.path.join(os.path.normpath(ws), "scenes")
            if os.path.isdir(s):
                return s
            return os.path.normpath(ws)
    except Exception:
        pass
    return ""


def _is_temp_dir(d):
    """Heuristic: return True if *d* looks like a temp/scratch directory."""
    d = os.path.normpath(os.path.abspath(d)).lower()
    import tempfile
    tmp = os.path.normpath(tempfile.gettempdir()).lower()
    if d.startswith(tmp):
        return True
    if os.path.basename(d).startswith("maya_vc_"):
        return True
    return False


def get_plugin_repo_hash():
    """Return short version identifier for the MayaVC plugin itself.

    1. Try ``git rev-parse HEAD`` first (subprocess, no _git helper).
    2. Fall back to cached .mayavc/plugin_version in the plugin directory.
    3. Cache the git result for future fallback calls.
    4. Return empty string if nothing works.
    """
    import sys
    for p in sys.path:
        try:
            if not os.path.isdir(p) or not os.path.isfile(os.path.join(p, "shelf_main.py")):
                continue
            ver_file = os.path.join(p, ".mayavc", "plugin_version")
            # Priority 1: git (always current)
            kwargs = {}
            if platform.system() == "Windows":
                kwargs["creationflags"] = 0x08000000
            try:
                r = subprocess.run(
                    ["git", "-c", "core.quotepath=false", "rev-parse", "HEAD"],
                    cwd=p, capture_output=True, text=True, timeout=10,
                    encoding="utf-8", errors="replace", **kwargs,
                )
                if r.returncode == 0 and r.stdout.strip():
                    h = r.stdout.strip()[:7]
                    # Update cache
                    try:
                        os.makedirs(os.path.join(p, ".mayavc"), exist_ok=True)
                        with open(ver_file, "w", encoding="utf-8") as fh:
                            fh.write(h)
                    except Exception:
                        pass
                    return h
            except Exception:
                pass
            # Priority 2: cached version file (git not available)
            if os.path.isfile(ver_file):
                with open(ver_file, "r", encoding="utf-8") as fh:
                    return fh.read().strip()[:7]
            break
        except Exception:
            pass
    return ""


# ---------------------------------------------------------------------------
# Version number
# ---------------------------------------------------------------------------

_VER_RE = re.compile(r"^(.*?)(_v(\d{3,}))\.(ma|mb)$", re.IGNORECASE)


def _parse_ver(filename):
    """Return (base, ext, ver) — ver=0 means no _vNNN suffix."""
    m = _VER_RE.match(os.path.basename(filename))
    if m:
        return m.group(1), m.group(4), int(m.group(3))
    root, ext = os.path.splitext(os.path.basename(filename))
    return root, ext.lstrip("."), 0


def detect_next_version(scenes_dir, base=None):
    """Return (base, ext, next_ver).

    If *base* is given and differs from the currently-open scene's base
    name, scanning starts from 1 (new naming branch).
    Otherwise the next sequential version for the current base is returned.
    """
    p = cmds.file(q=True, sn=True)
    if p:
        _, ext = os.path.splitext(p)
        ext = ext.lstrip(".")
    else:
        ext = "mb"

    current_base = _parse_ver(p)[0] if p else ""

    if base is None:
        base = current_base or "untitled"

    max_ver = 0
    try:
        for f in os.listdir(scenes_dir):
            if not f.lower().endswith((".ma", ".mb")):
                continue
            b, e, v = _parse_ver(f)
            if b == base and e == ext and v > max_ver:
                max_ver = v
    except Exception:
        pass
    return base, ext, max_ver + 1


# ---------------------------------------------------------------------------
# Core: save + commit
# ---------------------------------------------------------------------------

@perf_timed()
def incremental_save(scenes_dir):
    """Save-as _vNNN, return new path or None."""
    base, ext, ver = detect_next_version(scenes_dir)
    new_path = os.path.join(scenes_dir, f"{base}_v{ver:03d}.{ext}")
    ft = "mayaAscii" if ext == "ma" else "mayaBinary"
    try:
        with perf_scope("maya_file_rename"):
            cmds.file(rename=new_path)
        with perf_scope("maya_file_save"):
            cmds.file(save=True, type=ft)
        return new_path
    except Exception as e:
        cmds.warning(f"MayaVC: save failed - {e}")
        return None


def dry_run_next_version(scenes_dir):
    """Return (base, ext, next_ver, preview_path) without touching disk."""
    base, ext, ver = detect_next_version(scenes_dir)
    new_path = os.path.join(scenes_dir, f"{base}_v{ver:03d}.{ext}")
    return base, ext, ver, new_path


# ---------------------------------------------------------------------------
# JSON persistence layer
# ---------------------------------------------------------------------------

def _vc_dir(scenes_dir):
    """Return path to .mayavc metadata directory (does NOT create it)."""
    return os.path.join(scenes_dir, ".mayavc")


def _ensure_vc_dir(scenes_dir):
    """Create .mayavc/ if missing. Return the directory path."""
    d = _vc_dir(scenes_dir)
    os.makedirs(d, exist_ok=True)
    return d


def _read_versions(scenes_dir):
    """Read .mayavc/versions.json → dict (tag → entry).  Empty dict on failure."""
    json_path = os.path.join(_vc_dir(scenes_dir), "versions.json")
    if not os.path.isfile(json_path):
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, IOError, OSError):
        return {}


def _lock_file(scenes_dir):
    """Acquire exclusive advisory lock on .mayavc/versions.lock.

    Returns the lock file handle, or None on failure.
    """
    _ensure_vc_dir(scenes_dir)
    lock_path = os.path.join(_vc_dir(scenes_dir), "versions.lock")
    try:
        fh = open(lock_path, "w")
        if platform.system() == "Windows":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return fh
    except (IOError, OSError):
        return None


def _unlock_file(lock_fh):
    """Release advisory lock and close the file handle."""
    if lock_fh is None:
        return
    try:
        if platform.system() == "Windows":
            import msvcrt
            msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()
    except Exception:
        pass


def _write_versions_atomic(scenes_dir, data):
    """Atomically write versions dict to JSON (caller must hold the lock).

    Writes to a .tmp file then calls os.replace() for atomic replacement.
    """
    vc_dir = _vc_dir(scenes_dir)
    json_path = os.path.join(vc_dir, "versions.json")
    tmp_path = json_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_path, json_path)
        return True
    except Exception as e:
        cmds.warning(f"MayaVC: failed to write versions.json - {e}")
        return False


def _acquire_and_read(scenes_dir):
    """Acquire lock, read versions dict. Returns (lock_fh, data_dict).

    If lock fails, returns (None, {}).
    """
    lock = _lock_file(scenes_dir)
    if lock is None:
        cmds.warning("MayaVC: Could not acquire lock on versions.json — concurrent access?")
        return None, {}
    return lock, _read_versions(scenes_dir)


# ---------------------------------------------------------------------------
# VersionRecord
# ---------------------------------------------------------------------------

class VersionRecord:
    __slots__ = ("tag", "date", "message", "file", "hash")

    def __init__(self, tag, date, message, file="", hash=""):
        self.tag = tag
        self.date = date
        self.message = message
        self.file = file       # basename of the scene file
        self.hash = hash       # always "" in JSON mode; kept for backward compat


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@perf_timed()
def vc_commit(scenes_dir, file_path, version, message):
    """Record a version in .mayavc/versions.json.  Returns True on success."""
    _ensure_vc_dir(scenes_dir)
    fname = os.path.basename(file_path)
    base, _, _ = _parse_ver(fname)
    tag = f"{base}_v{version:03d}"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    msg_line = f"[{now_str}] {message.strip()}"

    lock, data = _acquire_and_read(scenes_dir)
    if lock is None:
        return False

    try:
        if tag in data:
            entry = data[tag]
            entry["messages"].append(msg_line)
            # Update file / time to reflect latest save
            entry["file"] = fname
            entry["time"] = now_str
        else:
            data[tag] = {
                "tag": tag,
                "file": fname,
                "time": now_str,
                "messages": [msg_line],
            }
        return _write_versions_atomic(scenes_dir, data)
    finally:
        _unlock_file(lock)


@perf_timed()
def vc_amend_commit(scenes_dir, file_path, version, append_message):
    """Append a timestamped message to an existing version WITHOUT bumping the
    version number.  Used by "Save w/ Commit" and "Save w/ Commit and Load".

    Returns True on success.
    """
    _ensure_vc_dir(scenes_dir)
    fname = os.path.basename(file_path)
    base, _, _ = _parse_ver(fname)
    tag = f"{base}_v{version:03d}"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    append_line = f"[{now_str}] {append_message.strip()}"

    lock, data = _acquire_and_read(scenes_dir)
    if lock is None:
        return False

    try:
        entry = data.get(tag)
        if entry is None:
            # Tag doesn't exist yet — create it as a single entry
            data[tag] = {
                "tag": tag,
                "file": fname,
                "time": now_str,
                "messages": [append_line],
            }
        else:
            entry["messages"].append(append_line)
            entry["file"] = fname
            entry["time"] = now_str
        return _write_versions_atomic(scenes_dir, data)
    finally:
        _unlock_file(lock)


@perf_timed()
def get_history(scenes_dir, scene_name=None):
    """Return list of VersionRecord, newest first, from versions.json.

    Args:
        scenes_dir: Path to the scenes/ directory.
        scene_name: If given, filter to versions of this scene base name
                    (e.g. "hero" matches hero_v001, hero_v005).
                    If None/empty, show all versions across all scenes.
    """
    data = _read_versions(scenes_dir)
    if not data:
        return []

    filter_base = (scene_name or "").lower()

    # Build a map: lowercased "base_vnnn" → actual filename from disk
    disk_files = {}
    try:
        for f in os.listdir(scenes_dir):
            low = f.lower()
            if low.endswith((".ma", ".mb")):
                root, _ = os.path.splitext(low)
                disk_files[root] = f
    except Exception:
        pass

    records = []
    for tag, entry in data.items():
        # Validate tag format: {base}_vNNN
        tag_ver = re.match(r'^(.+)_v(\d{3,})$', tag)
        if not tag_ver:
            continue
        if filter_base and tag_ver.group(1).lower() != filter_base:
            continue

        # File name: prefer what's on disk, fall back to stored value
        tag_file = disk_files.get(tag.lower(), "") or entry.get("file", "")

        # Date: file mtime > stored time
        date_str = ""
        if tag_file:
            file_path = os.path.join(scenes_dir, tag_file)
            if os.path.isfile(file_path):
                date_str = datetime.datetime.fromtimestamp(
                    os.path.getmtime(file_path)).strftime("%Y-%m-%d %H:%M")
        if not date_str:
            date_str = entry.get("time", "")

        # Message: join all message lines with newlines (history browser
        # splits on "\n" to detect multi-commit entries)
        messages = entry.get("messages", [])
        full_msg = "\n".join(messages) if messages else ""

        records.append(VersionRecord(
            tag=tag,
            date=date_str,
            message=full_msg,
            file=tag_file,
            hash="",
        ))

    # Sort by time descending (newest first), tiebreak by tag name descending
    records.sort(key=lambda r: (r.date, r.tag), reverse=True)
    return records


@perf_timed()
def delete_version(scenes_dir, tag, filename):
    """Delete a version: remove entry from JSON and delete the scene file.

    Returns True on success, False on failure.
    """
    lock, data = _acquire_and_read(scenes_dir)
    if lock is None:
        return False

    try:
        if tag not in data:
            cmds.warning(f"MayaVC: tag {tag} not found in versions.json")
            return False
        del data[tag]
        if not _write_versions_atomic(scenes_dir, data):
            return False
    finally:
        _unlock_file(lock)

    # Remove physical file from disk
    file_path = os.path.join(scenes_dir, filename)
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
    except Exception as e:
        cmds.warning(f"MayaVC: failed to delete file - {e}")
        return False

    cmds.warning(f"MayaVC: deleted {tag} ({filename})")
    return True


@perf_timed()
def load_version(scenes_dir, tag):
    """Open the scene file for *tag* in Maya.

    The file must exist on disk in the scenes directory (no git extraction).
    Shows a confirmation dialog (Save / Commit-and-Load / Load-without-saving).
    """
    data = _read_versions(scenes_dir)
    entry = data.get(tag)
    if not entry:
        cmds.warning(f"MayaVC: tag {tag} not found")
        return False

    tag_file = entry.get("file", "")
    if not tag_file:
        cmds.warning(f"MayaVC: no file recorded for tag {tag}")
        return False

    target_path = os.path.join(scenes_dir, tag_file)
    if not os.path.isfile(target_path):
        cmds.warning(f"MayaVC: file not found on disk — {tag_file}")
        return False

    _, ext = os.path.splitext(tag_file)
    is_binary = ext.lower() == ".mb"

    # Confirm dialog
    result = cmds.confirmDialog(
        title="Load Historical Version",
        message=f"Load {tag}: {tag_file}?\n\nSave current scene first?",
        button=["Save w/ Commit and Load", "Save and Load",
                "Load without Saving", "Cancel"],
        defaultButton="Save and Load",
        cancelButton="Cancel",
        dismissString="Cancel",
    )
    if result == "Cancel":
        return False

    if result in ("Save and Load", "Save w/ Commit and Load"):
        cur = cmds.file(q=True, sn=True)
        cur_base, _, cur_ver = _parse_ver(os.path.basename(cur)) if cur else ("", "", 0)

        if result == "Save w/ Commit and Load" and cur_ver > 0:
            user_msg = cmds.promptDialog(
                title="Commit Message",
                message=f"Describe this save (appended to {cur_base}_v{cur_ver:03d}):",
                button=["Commit", "Cancel"],
                defaultButton="Commit",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if user_msg == "Cancel":
                return False
            append_msg = cmds.promptDialog(q=True, text=True) or ""

            try:
                with perf_scope("maya_file_save"):
                    mel.eval("file -save -f")
                cmds.warning(f"MayaVC: saved to {cur}")
            except Exception as e:
                cmds.warning(f"MayaVC: save failed - {e}")
                return False

            if append_msg.strip():
                vc_amend_commit(scenes_dir, cur, cur_ver, append_msg.strip())
                cmds.warning(f"MayaVC: commit appended to {cur_base}_v{cur_ver:03d}")
            else:
                cmds.warning("MayaVC: empty message — commit skipped, file saved")
        else:
            try:
                with perf_scope("maya_file_save"):
                    mel.eval("file -save -f")
                cmds.warning(f"MayaVC: saved to {cur}")
            except Exception as e:
                cmds.warning(f"MayaVC: save failed - {e}")

    # Open the target version
    try:
        with perf_scope("maya_file_open"):
            cmds.file(target_path, open=True, force=True)
        return True
    except Exception:
        try:
            cmds.file(target_path, i=True,
                      type="mayaBinary" if is_binary else "mayaAscii")
            return True
        except Exception as e:
            cmds.warning(f"MayaVC: open failed - {e}")
            return False


def get_repo_status(scenes_dir):
    """Return a status summary dict consumed by ui/status_widget.py.

    Keys: is_repo, total_versions, current_version, last_commit_time,
          last_commit_message.
    """
    result = {
        "is_repo": False,
        "total_versions": 0,
        "current_version": 0,
        "last_commit_time": "",
        "last_commit_message": "",
    }

    data = _read_versions(scenes_dir)
    if not data:
        return result

    result["is_repo"] = True
    result["total_versions"] = len(data)

    # Find most recent entry by time
    newest = None
    for entry in data.values():
        t = entry.get("time", "")
        if newest is None or t > newest.get("time", ""):
            newest = entry

    if newest:
        result["last_commit_time"] = newest.get("time", "")
        msgs = newest.get("messages", [])
        if msgs:
            result["last_commit_message"] = msgs[-1]

    # Current version from open Maya file
    try:
        p = cmds.file(q=True, sn=True)
        if p:
            _, _, ver = _parse_ver(os.path.basename(p))
            result["current_version"] = ver
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Backward-compat aliases (so existing callers don't break)
# ---------------------------------------------------------------------------
git_commit = vc_commit
git_amend_commit = vc_amend_commit
