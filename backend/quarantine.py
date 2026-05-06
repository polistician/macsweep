"""Smart deletion = move to a dated quarantine folder, keep a manifest, allow
restore for 30 days, then auto-purge.

Nothing is ever rm'd directly. The quarantine path mirrors the original path
under ~/.macsweep_quarantine/<timestamp>/, so the original location is obvious
when reviewing.
"""
import os
import shutil
import time
from pathlib import Path

from . import db

HOME = str(Path.home())
QUARANTINE_ROOT = Path(HOME) / ".macsweep_quarantine"
PURGE_DAYS = 30

# Hard guards — never quarantine anything below these prefixes, regardless of
# what the caller passes in.
PROTECTED_PREFIXES = (
    "/System", "/usr", "/bin", "/sbin", "/private", "/dev", "/Volumes",
    "/Library",  # /Library is system-managed; ~/Library is fine
    f"{HOME}/Library/Mobile Documents",
    f"{HOME}/Library/CloudStorage",
    str(QUARANTINE_ROOT),
)


def _is_protected(path):
    return any(path == p or path.startswith(p + "/") for p in PROTECTED_PREFIXES)


def _range_bounds(path: str) -> tuple[str, str]:
    """Inclusive lower / exclusive upper bound covering everything under `path/`.
    Lets DB queries use the implicit index on `files.path` (BINARY collation)
    via a range comparison, which `LIKE 'X/%'` cannot."""
    prefix = path.rstrip("/") + "/"
    return prefix, prefix[:-1] + chr(ord("/") + 1)


def _size_from_db(conn, path: str) -> int | None:
    """Return SUM(size) for the path and everything underneath it. None if the
    DB has no rows covering this path — caller should fall back to fs walk."""
    lo, hi = _range_bounds(path)
    row = conn.execute(
        "SELECT COALESCE(SUM(size), 0) AS s, COUNT(*) AS n "
        "FROM files WHERE path = ? OR (path >= ? AND path < ?)",
        (path, lo, hi),
    ).fetchone()
    return row["s"] if row and row["n"] else None


def _size_from_fs(path: str) -> int:
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for dp, _, fns in os.walk(path, followlinks=False, onerror=lambda _e: None):
        for f in fns:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return total


def quarantine(paths):
    QUARANTINE_ROOT.mkdir(parents=True, exist_ok=True)
    batch_dir = QUARANTINE_ROOT / time.strftime("%Y%m%d-%H%M%S")
    batch_dir.mkdir(exist_ok=True)

    now = time.time()
    purge_after = now + PURGE_DAYS * 86_400
    results = []

    with db.connect() as conn:
        for original in paths:
            if _is_protected(original):
                results.append({"path": original, "status": "protected"})
                continue
            if not os.path.lexists(original):
                results.append({"path": original, "status": "missing"})
                continue

            try:
                size = _size_from_db(conn, original)
                # DB may not have a row for this path (e.g., scanner skipped
                # it) or every row may be zero-sized. Either way, walk the
                # filesystem so the freed-size reported to the user is real.
                if not size:
                    size = _size_from_fs(original)
                cat_row = conn.execute(
                    "SELECT category FROM files WHERE path = ?", (original,)
                ).fetchone()
                category = cat_row["category"] if cat_row else None

                target = batch_dir / original.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(original, target)

                conn.execute(
                    """INSERT INTO quarantine
                       (original_path, quarantine_path, size, category,
                        quarantined_at, purge_after, status)
                       VALUES (?, ?, ?, ?, ?, ?, 'quarantined')""",
                    (original, str(target), size, category, now, purge_after),
                )
                lo, hi = _range_bounds(original)
                conn.execute(
                    "DELETE FROM files WHERE path = ? OR (path >= ? AND path < ?)",
                    (original, lo, hi),
                )
                results.append({"path": original, "size": size, "status": "quarantined"})
            except Exception as e:
                results.append({"path": original, "status": "error", "error": str(e)})

    # Drop the warmup cache so Smart Scan / overview / sunburst recompute live
    # against the now-updated files table. Then re-warm in the background.
    if any(r["status"] == "quarantined" for r in results):
        from . import warmup
        warmup.invalidate()
        warmup.warm_async()

    return results


def restore(qid):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM quarantine WHERE id=? AND status='quarantined'", (qid,)
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        if os.path.lexists(row["original_path"]):
            return {"status": "conflict", "msg": "original path already exists"}
        os.makedirs(os.path.dirname(row["original_path"]), exist_ok=True)
        shutil.move(row["quarantine_path"], row["original_path"])
        conn.execute("UPDATE quarantine SET status='restored' WHERE id=?", (qid,))
        return {"status": "restored", "path": row["original_path"]}


def purge(qid):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM quarantine WHERE id=? AND status='quarantined'", (qid,)
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        qp = row["quarantine_path"]
        if os.path.isdir(qp):
            shutil.rmtree(qp, ignore_errors=True)
        elif os.path.lexists(qp):
            os.remove(qp)
        conn.execute("UPDATE quarantine SET status='purged' WHERE id=?", (qid,))
        return {"status": "purged", "size": row["size"]}


def list_quarantined():
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM quarantine WHERE status='quarantined' ORDER BY quarantined_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def auto_purge():
    now = time.time()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM quarantine WHERE status='quarantined' AND purge_after < ?",
            (now,),
        ).fetchall()
    for r in rows:
        purge(r["id"])
    return len(rows)
