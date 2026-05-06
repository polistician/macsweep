"""Sweep-mode candidate queue.

Sweep is the killer feature — Tinder-style review of files where auto-cleanup
isn't safe but the user almost certainly doesn't need them. Ranks candidates
by likelihood-to-trash: large + old + non-regenerable wins.

Pulls from forgotten files, old downloads, and stale large media.
"""
import time

from . import db


def queue(limit: int = 200) -> list[dict]:
    now = time.time()
    year = now - 365 * 86_400
    six_mo = now - 180 * 86_400

    with db.connect() as conn:
        rows = conn.execute("""
            SELECT path, size, mtime, atime, category, subcategory
            FROM files
            WHERE
                -- Personal-files territory: never auto-deleted, but worth manual review
                category NOT IN ('cache', 'dev_artifact', 'trash', 'logs')
                AND size > 50 * 1024 * 1024     -- >50 MB
                AND atime < ?                    -- not opened in 6+ months
            ORDER BY
                -- Bigger + older = higher priority. Score = size_GB * months_idle.
                (size / (1024.0*1024.0*1024.0)) * ((? - atime) / (30.0*86400.0)) DESC
            LIMIT ?
        """, (six_mo, now, limit)).fetchall()
        return [dict(r) for r in rows]
