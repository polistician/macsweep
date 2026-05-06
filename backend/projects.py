"""Detect git repos from the file index and tally source vs. artifact size.

A "stale" project is one whose non-artifact files haven't been touched in 6+
months — strong signal that its `node_modules` / `target` / `.venv` are pure
waste. Used by the suggestions engine.
"""
import os
import time

from . import db


def detect_from_index():
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT DISTINCT REPLACE(path, '/.git/HEAD', '') AS root
            FROM files
            WHERE path LIKE '%/.git/HEAD'
        """).fetchall()
        roots = [r["root"] for r in rows if r["root"]]

        now = time.time()
        conn.execute("DELETE FROM projects")

        for root in roots:
            stats = _stats(conn, root)
            conn.execute(
                """INSERT INTO projects
                   (path, name, last_activity, source_size, artifact_size, scanned_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (root, os.path.basename(root) or root,
                 stats["last_activity"], stats["source"], stats["artifact"], now),
            )


def _stats(conn, root):
    rows = conn.execute(
        """SELECT size, mtime, category FROM files WHERE path LIKE ? || '/%'""",
        (root,),
    ).fetchall()

    artifact = 0
    source = 0
    last_activity = 0.0
    for r in rows:
        if r["category"] == "dev_artifact":
            artifact += r["size"]
        else:
            source += r["size"]
            if r["mtime"] > last_activity:
                last_activity = r["mtime"]
    return {"artifact": artifact, "source": source, "last_activity": last_activity}
