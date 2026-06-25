"""
core/vc_engine.py - Maya Version Control core engine
Single responsibility: save file -> git commit + tag -> list tags.
"""

import os
import re
import subprocess
import datetime
import platform

import maya.cmds as cmds
import maya.mel as mel

from core.perf_monitor import perf_timed, perf_scope

# Windows: no cmd.exe popup; force utf-8 encoding
if platform.system() == "Windows":
    _SUB_KWARGS = dict(capture_output=True, text=True, timeout=15,
                       creationflags=0x08000000, encoding="utf-8", errors="replace")
else:
    _SUB_KWARGS = dict(capture_output=True, text=True, timeout=15,
                       encoding="utf-8", errors="replace")


def _git(args, cwd, binary=False):
    """Run git, return stdout on success, None on failure.

    IMPORTANT: Returns None on failure (not empty string), so callers can
    distinguish between "git add succeeded with no output" and "git failed".
    When checking for failure, use `if _git(...) is None`.
    """
    cmd_label = f"git {' '.join(args[:2])}"  # e.g. "git tag -l", "git commit -m"
    kwargs = dict(_SUB_KWARGS)
    if binary:
        kwargs.pop("text", None)
        kwargs.pop("encoding", None)
        kwargs.pop("errors", None)
    try:
        with perf_scope(cmd_label):
            r = subprocess.run(
                ["git", "-c", "core.quotepath=false"] + args,
                cwd=cwd, **kwargs,
            )
        if r.returncode != 0:
            return None
        if binary:
            return r.stdout or b""
        return (r.stdout or "").strip()
    except Exception:
        return None


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
        # Don't use temp directories — they aren't the real project
        if d and not _is_temp_dir(d):
            return d
    # fallback: Maya project scenes/
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
    # also catch "maya_vc_" our own temp prefix
    if os.path.basename(d).startswith("maya_vc_"):
        return True
    return False


def get_plugin_repo_hash():
    """Return short hash (12 chars) of the MayaVC plugin's own git repo.

    Used for window titles to identify which version of the tool is running.
    Returns empty string if the plugin isn't in a git repo.
    """
    import sys
    # Locate package root via sys.path — the dir that contains shelf_main.py
    for p in sys.path:
        try:
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, "shelf_main.py")):
                h = _git(["rev-parse", "HEAD"], cwd=p)
                if h and "fatal" not in h:
                    return h[:7]
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
        ext = "ma"

    current_base = _parse_ver(p)[0] if p else ""

    if base is None:
        base = current_base or "untitled"

    # Always scan the given base from scratch; if the user chose a different
    # name, the loop naturally finds nothing and returns 1.
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


def _ensure_git(scenes_dir):
    """git init if needed; set user.name / user.email if missing.

    We check for a .git directory INSIDE scenes_dir specifically, because
    git rev-parse --is-inside-work-tree would walk up to a parent repo
    (e.g. the MayaVC plugin repo itself) and falsely report 'true'.
    """
    if not os.path.isdir(os.path.join(scenes_dir, ".git")):
        _git(["init"], cwd=scenes_dir)

    if not _git(["-C", scenes_dir, "config", "user.name"], cwd=scenes_dir) \
       and not _git(["-C", scenes_dir, "config", "--global", "user.name"], cwd=scenes_dir):
        try:
            import getpass
            u = getpass.getuser()
        except Exception:
            u = "maya-artist"
        _git(["-C", scenes_dir, "config", "user.name", u], cwd=scenes_dir)
    if not _git(["-C", scenes_dir, "config", "user.email"], cwd=scenes_dir) \
       and not _git(["-C", scenes_dir, "config", "--global", "user.email"], cwd=scenes_dir):
        try:
            import getpass
            u = getpass.getuser()
        except Exception:
            u = "maya-artist"
        _git(["-C", scenes_dir, "config", "user.email", f"{u}@maya-vc.local"], cwd=scenes_dir)


@perf_timed()
def git_commit(scenes_dir, file_path, version, message):
    """git add + commit + tag.  Returns True on success."""
    _ensure_git(scenes_dir)
    fname = os.path.basename(file_path)
    base, _, _ = _parse_ver(fname)
    tag = f"{base}_v{version:03d}"
    full_msg = f"{tag}: {message.strip()}"

    # add
    out = _git(["add", fname], cwd=scenes_dir)
    if out is None:
        cmds.warning("MayaVC: git add failed")
        return False

    # commit (allow-empty so the tag always gets force-updated)
    r = _git(["commit", "--allow-empty", "-m", full_msg], cwd=scenes_dir)
    if r is None:
        cmds.warning("MayaVC: git commit failed")
        return False

    # tag
    _git(["tag", "-f", tag], cwd=scenes_dir)
    return True


# ---------------------------------------------------------------------------
# History (simple: just read tags)
# ---------------------------------------------------------------------------

class VersionRecord:
    __slots__ = ("tag", "date", "message", "file", "hash")
    def __init__(self, tag, date, message, file="", hash=""):
        self.tag = tag
        self.date = date
        self.message = message
        self.file = file        # basename of the scene file stored in this tag
        self.hash = hash        # short commit hash (7 chars)


@perf_timed()
def get_history(scenes_dir, scene_name=None):
    """Return list of VersionRecord, newest first.

    Uses 'git tag -l --sort=-creatordate' then 'git log -1 --format=%ai|%s <tag>'
    for each tag.  This is O(tags) but for a single-artist Maya project
    that's tens of tags, not hundreds.

    Args:
        scenes_dir: Path to the scenes/ git repo.
        scene_name: If given, filter to versions of this scene base name
                    (e.g. "hero" matches hero_v001.ma, hero_v005.mb).
                    If None/empty, show all versions across all scenes.
    """
    if not os.path.isdir(os.path.join(scenes_dir, ".git")):
        return []

    filter_base = (scene_name or "").lower()

    # --- 1) Single git call: all tag metadata ---
    refs_raw = _git([
        "for-each-ref",
        "--sort=-version:refname",
        "--format=%(refname:short)|%(objectname:short)|%(contents:subject)",
        "refs/tags",
    ], cwd=scenes_dir)

    if not refs_raw:
        return []

    # Parse: tag -> (hash, message)
    tag_meta = {}  # tag -> (hash, message)
    for line in refs_raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) >= 3:
            tag_meta[parts[0]] = (parts[1][:7], parts[2].strip())

    # --- 2) Match tags to files on disk (no extra git calls) ---
    # Scan scenes_dir once. Map "base_vNNN" -> filename for quick lookup.
    disk_files = {}  # lowercased "base_vnnn" -> actual filename
    try:
        for f in os.listdir(scenes_dir):
            low = f.lower()
            if low.endswith((".ma", ".mb")):
                # Extract {base}_vNNN part from filename (strip extension)
                root, _ = os.path.splitext(low)
                disk_files[root] = f
    except Exception:
        pass

    records = []
    for tag, (commit_hash, msg) in tag_meta.items():
        tag_ver = re.match(r'^(.+)_v(\d{3,})$', tag)
        if not tag_ver:
            continue
        if filter_base and tag_ver.group(1).lower() != filter_base:
            continue

        # File name: look up on disk first, fall back to git ls-tree
        tag_file = disk_files.get(tag.lower(), "")
        if not tag_file:
            # Rare: file not on disk, one-off git ls-tree
            ls = _git(["ls-tree", "-r", "--name-only", tag], cwd=scenes_dir)
            if ls:
                for f in ls.split("\n"):
                    if f.lower().endswith((".ma", ".mb")):
                        tag_file = f.strip()
                        break

        # Date: scene file mtime > tag ref mtime
        date_str = ""
        if tag_file:
            file_path = os.path.join(scenes_dir, tag_file)
            if os.path.isfile(file_path):
                date_str = datetime.datetime.fromtimestamp(
                    os.path.getmtime(file_path)).strftime("%Y-%m-%d %H:%M")
        if not date_str:
            ref_path = os.path.join(scenes_dir, ".git", "refs", "tags", tag)
            if os.path.isfile(ref_path):
                date_str = datetime.datetime.fromtimestamp(
                    os.path.getmtime(ref_path)).strftime("%Y-%m-%d %H:%M")

        records.append(VersionRecord(
            tag=tag, date=date_str, message=msg, file=tag_file, hash=commit_hash,
        ))

    return records


def delete_version(scenes_dir, tag, filename):
    """Delete a version: remove file from disk and delete git tag.

    This is destructive — the .ma/.mb file is permanently removed from the
    scenes directory and the git tag is deleted so it no longer appears in
    the version history.

    Returns True on success, False on failure.
    """
    file_path = os.path.join(scenes_dir, filename)

    # 1. Delete the git tag first (reversible-ish: git reflog)
    if _git(["tag", "-d", tag], cwd=scenes_dir) is None:
        cmds.warning(f"MayaVC: failed to delete tag {tag}")
        return False

    # 2. Remove the physical file
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
    """Checkout scene file at tag into scenes/ and open in Maya."""
    # find the .ma/.mb file
    files = _git(["ls-tree", "-r", "--name-only", tag], cwd=scenes_dir)
    if files is None:
        cmds.warning(f"MayaVC: tag {tag} not found")
        return False
    scene_files = [f for f in files.split("\n") if f.lower().endswith((".ma", ".mb"))]
    if not scene_files:
        cmds.warning(f"MayaVC: no .ma/.mb in tag {tag}")
        return False

    target = scene_files[0]
    # If multiple scene files in this commit (the usual case after v001),
    # pick the one whose version suffix matches this tag (hero_v005 → _v005.)
    if len(scene_files) > 1:
        m = re.search(r'_v(\d{3,})$', tag)
        if m:
            version_suffix = f"_v{m.group(1)}."
            for f in scene_files:
                if version_suffix in f:
                    target = f
                    break
    _, ext = os.path.splitext(target)
    is_binary = ext.lower() == ".mb"

    result = cmds.confirmDialog(
        title="Load Historical Version",
        message=f"Load {tag}: {target}?\n\nSave current scene first?",
        button=["Save and Load", "Load without Saving", "Cancel"],
        defaultButton="Save and Load",
        cancelButton="Cancel",
        dismissString="Cancel",
    )
    if result == "Cancel":
        return False
    if result == "Save and Load":
        cur = cmds.file(q=True, sn=True)
        try:
            with perf_scope("maya_file_save"):
                mel.eval("file -save -f")
            cmds.warning(f"MayaVC: saved to {cur}")
        except Exception as e:
            cmds.warning(f"MayaVC: save failed - {e}")
        # Sync tag file mtime with the saved scene file so they stay aligned
        cur_base, _, cur_ver = _parse_ver(os.path.basename(cur))
        if cur_ver > 0:
            tag_path = os.path.join(scenes_dir, ".git", "refs", "tags",
                                    f"{cur_base}_v{cur_ver:03d}")
            if os.path.isfile(tag_path) and os.path.isfile(cur):
                try:
                    os.utime(tag_path, (os.path.getmtime(cur), os.path.getmtime(cur)))
                except Exception:
                    pass

    # Open the target version from disk (it's already in scenes/).
    # Only fall back to git extraction if the file doesn't exist on disk.
    checkout_path = os.path.join(scenes_dir, target)
    if os.path.isfile(checkout_path):
        with perf_scope("maya_file_open"):
            cmds.file(checkout_path, open=True, force=True)
        return True

    # Rare: file not on disk — extract from git
    if is_binary:
        try:
            r = subprocess.run(
                ["git", "-c", "core.quotepath=false", "show", f"{tag}:{target}"],
                cwd=scenes_dir,
                capture_output=True, timeout=15,
                **({"creationflags": 0x08000000} if platform.system() == "Windows" else {}),
            )
            if r.returncode == 0 and r.stdout:
                content_bytes = r.stdout
            else:
                cmds.warning(f"MayaVC: could not read {tag}:{target}")
                return False
        except Exception:
            cmds.warning(f"MayaVC: could not read {tag}:{target}")
            return False
    else:
        content = _git(["show", f"{tag}:{target}"], cwd=scenes_dir)
        if not content:
            cmds.warning(f"MayaVC: could not read {tag}:{target}")
            return False
        content_bytes = content.encode("utf-8")

    with open(checkout_path, "wb") as f:
        f.write(content_bytes)

    try:
        with perf_scope("maya_file_open"):
            cmds.file(checkout_path, open=True, force=True)
        return True
    except Exception:
        try:
            cmds.file(checkout_path, i=True,
                      type="mayaBinary" if is_binary else "mayaAscii")
            return True
        except Exception as e:
            cmds.warning(f"MayaVC: open failed - {e}")
            return False
