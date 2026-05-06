"""Filesystem walker — populates the `files` table.

Walks the user's home directory, classifying each file via `rules.classify`
and inserting in 1k-row batches. Skips system paths, iCloud placeholders, and
the inside of `.app` bundles (counted as one entry).
"""
import json
import os
import stat
import time
from pathlib import Path

from . import db, rules

HOME = str(Path.home())

# Bump when the indexer's semantics change (e.g. size calculation, new categories,
# etc.). On launch, if the latest done scan was indexed with an older version,
# we auto-trigger a fresh scan so the user doesn't see stale/inflated numbers.
#   v1: initial release (st.st_size, may double-count cloud placeholders)
#   v2: switched to st.st_blocks * 512 — accurate disk usage
INDEX_VERSION = 2


# Cheap path-prefix tests used to derive app_signals during the walk
APP_SIGNAL_TESTS = {
    "has_steam":     lambda p: "/Library/Application Support/Steam/" in p,
    "has_xcode":     lambda p: "/Library/Developer/Xcode/" in p,
    "has_docker":    lambda p: "/Library/Containers/com.docker.docker/" in p,
    "has_adobe":     lambda p: "/Library/Application Support/Adobe/" in p,
    "has_ollama":    lambda p: "/.ollama/" in p,
    "has_chrome":    lambda p: "/Library/Application Support/Google/Chrome/" in p,
    "has_brave":     lambda p: "/Library/Application Support/BraveSoftware/" in p,
    "has_firefox":   lambda p: "/Library/Application Support/Firefox/" in p,
    "has_slack":     lambda p: "/Library/Application Support/Slack/" in p,
    "has_discord":   lambda p: "/Library/Application Support/discord/" in p,
    "has_spotify":   lambda p: "/Library/Caches/com.spotify.client/" in p,
    "has_node":      lambda p: "/.npm/" in p or "/node_modules/" in p,
    "has_python":    lambda p: "/.venv/" in p or "/site-packages/" in p,
    "has_rust":      lambda p: "/.cargo/" in p,
    "has_go":        lambda p: "/go/pkg/" in p,
    "has_ios_devs":  lambda p: "/Library/Developer/CoreSimulator/" in p,
    "has_obsidian":  lambda p: "/Library/Application Support/obsidian/" in p,
    "has_unity":     lambda p: "/Library/Unity/" in p,
    "has_jetbrains": lambda p: "/Library/Application Support/JetBrains/" in p,
}

SKIP_PREFIXES = (
    f"{HOME}/Library/Mobile Documents",
    f"{HOME}/Library/CloudStorage",
    f"{HOME}/.macsweep_quarantine",
    "/System", "/private", "/usr", "/dev", "/Volumes", "/Network", "/cores", "/bin", "/sbin",
)

STATE = {
    "running": False,
    "phase": "idle",      # 'counting' | 'indexing' | 'done'
    "count": 0,            # files indexed so far
    "total": 0,            # files we expect to walk (from pre-pass)
    "size": 0,
    "current": "",
    "error": None,
    "started_at": 0.0,
}


def last_scan_finished_at() -> float | None:
    """Wall-clock of the latest done scan (or None)."""
    from . import db
    with db.connect() as conn:
        row = conn.execute(
            "SELECT finished_at FROM scan_meta WHERE status='done' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["finished_at"] if row and row["finished_at"] else None


def latest_index_version() -> int | None:
    """Returns the index_version of the most recent done scan, or None."""
    from . import db
    with db.connect() as conn:
        row = conn.execute(
            "SELECT index_version FROM scan_meta WHERE status='done' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return row["index_version"] or 1


def index_is_stale() -> bool:
    """True if the latest done scan was indexed with an older version of the
    code's semantics, OR there's no scan yet."""
    v = latest_index_version()
    return v is None or v < INDEX_VERSION


def _fast_count(root: str) -> int:
    """Count files quickly using os.scandir, without stat. ~6-10x faster than
    walking with stat calls. Used to give the user real progress %."""
    total = 0
    stack = [root]
    while stack:
        path = stack.pop()
        if _should_skip(path):
            continue
        if path.endswith(".app"):
            total += 1
            continue
        try:
            with os.scandir(path) as entries:
                for e in entries:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += 1
                    except OSError:
                        continue
        except (PermissionError, FileNotFoundError, OSError):
            continue
    return total


def scan(root=None):
    """Atomic scan: keep old data live until the new scan finishes.

    Returns the meta_id of the inserted scan_meta row, or None if a scan
    was already running. Caller should pass meta_id to any post-scan steps
    that need to update the row — never read it back from a global, because
    a stale meta_id from a previous run can corrupt the wrong scan_meta row.
    """
    if STATE["running"]:
        return None
    root = root or HOME
    started = time.time()
    STATE.update(running=True, phase="counting", count=0, total=0, size=0,
                  current="", error=None, started_at=started)

    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO scan_meta (started_at, status, index_version) VALUES (?, 'running', ?)",
            (started, INDEX_VERSION),
        )
        meta_id = cur.lastrowid

    # Phase 1: fast pre-count for real progress %. Takes ~5-15s on a 1.7M-file
    # home dir (no stat calls, just scandir). Beats fake estimation 100%.
    STATE["current"] = "Counting files…"
    STATE["total"] = _fast_count(root)
    STATE["phase"] = "indexing"

    # Prior 'scanned_at' value(s) of files we'll DELETE on success
    batch = []
    signals = {k: False for k in APP_SIGNAL_TESTS}

    try:
        with db.connect() as conn:
            for path, size, mtime, atime in _walk(root):
                category, sub, regen = rules.classify(path)
                batch.append((
                    path,
                    os.path.dirname(path),
                    os.path.basename(path),
                    os.path.splitext(path)[1].lower(),
                    size, mtime, atime,
                    category, sub, 1 if regen else 0, started,
                ))
                STATE["count"] += 1
                STATE["size"] += size
                STATE["current"] = path
                for key, test in APP_SIGNAL_TESTS.items():
                    if not signals[key] and test(path):
                        signals[key] = True

                if len(batch) >= 1000:
                    _flush(conn, batch)
                    batch.clear()
                    conn.execute(
                        "UPDATE scan_meta SET files_indexed=?, total_size=?, current_path=? WHERE id=?",
                        (STATE["count"], STATE["size"], STATE["current"], meta_id),
                    )
                    conn.commit()

            if batch:
                _flush(conn, batch)
                conn.commit()

            # Atomic swap: drop the previous generation now that this one is complete.
            conn.execute("DELETE FROM files WHERE scanned_at != ?", (started,))
            # status stays 'running' here. The orchestrator runs post-scan
            # steps (warmup, redundancy) then explicitly marks 'done'.
            conn.execute(
                "UPDATE scan_meta SET finished_at=?, files_indexed=?, total_size=?, app_signals=? WHERE id=?",
                (time.time(), STATE["count"], STATE["size"], json.dumps(signals), meta_id),
            )
            conn.commit()
        return meta_id
    except Exception as e:
        STATE["error"] = str(e)
        # Use the LOCAL meta_id (not from STATE) — corrupting a previous
        # successful scan_meta row was a serious bug.
        try:
            with db.connect() as conn:
                conn.execute("UPDATE scan_meta SET status='error', error=? WHERE id=?",
                              (str(e), meta_id))
                conn.execute("DELETE FROM files WHERE scanned_at = ?", (started,))
        except Exception:
            pass
        raise
    finally:
        STATE["running"] = False


def _flush(conn, batch):
    conn.executemany(
        """INSERT OR REPLACE INTO files
           (path, parent_dir, name, ext, size, mtime, atime, category, subcategory, regenerable, scanned_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        batch,
    )


def _should_skip(dirpath):
    return any(dirpath == p or dirpath.startswith(p + "/") for p in SKIP_PREFIXES)


def _disk_size(st) -> int:
    """Actual disk usage in bytes — matches `du`. Critical for cloud-on-demand
    placeholders (OneDrive, iCloud Drive) where st_size reports logical size
    but the file occupies just 4 KB on disk. Without this, indexes are 5-10×
    inflated for users with cloud sync."""
    blocks = getattr(st, "st_blocks", None)
    if blocks is not None:
        return blocks * 512
    return st.st_size


def _walk(root):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=lambda _e: None):
        if _should_skip(dirpath):
            dirnames[:] = []
            continue

        # Treat .app bundles as one opaque entry
        if dirpath.endswith(".app"):
            try:
                size = _dir_size(dirpath)
                st = os.lstat(dirpath)
                yield dirpath, size, st.st_mtime, st.st_atime
            except OSError:
                pass
            dirnames[:] = []
            continue

        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
                if not stat.S_ISREG(st.st_mode):
                    continue
                yield full, _disk_size(st), st.st_mtime, st.st_atime
            except (OSError, ValueError):
                continue


def _dir_size(path):
    total = 0
    for dp, _, fns in os.walk(path, followlinks=False, onerror=lambda _e: None):
        for f in fns:
            try:
                total += _disk_size(os.lstat(os.path.join(dp, f)))
            except OSError:
                pass
    return total
