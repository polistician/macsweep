"""Smart cleanup suggestions, derived from the indexed data.

Tier 1 (safe / regenerable): trash, caches, old logs, stale build artifacts.
Tier 2 (review): old downloads, installers, forgotten large files, ios backups.

Each suggestion exposes an `action` slug that the UI uses to fetch the matching
file list and let the user quarantine selectively.
"""
import time

from . import db

DAY = 86_400
MONTH_6 = 180 * DAY
YEAR = 365 * DAY


def generate():
    out = []
    now = time.time()

    with db.connect() as conn:
        out += _trash(conn)
        out += _caches(conn)
        out += _stale_artifacts(conn, now)
        out += _old_logs(conn, now)
        out += _installers(conn)
        out += _old_downloads(conn, now)
        out += _ios_backups(conn)
        out += _forgotten(conn, now)

    return sorted(out, key=lambda s: -s["size"])


def _trash(conn):
    s_row = conn.execute(
        "SELECT COALESCE(SUM(size), 0) AS s FROM files WHERE category='trash'"
    ).fetchone()
    if not s_row["s"]:
        return []
    # Count top-level trash items (one per direct child of ~/.Trash), not the
    # files indexed inside them. A 4-file .app bundle is one item.
    n_row = conn.execute("""
        SELECT COUNT(DISTINCT
            substr(substr(path, instr(path, '/.Trash/') + 8),
                   1,
                   CASE WHEN instr(substr(path, instr(path, '/.Trash/') + 8), '/') > 0
                        THEN instr(substr(path, instr(path, '/.Trash/') + 8), '/') - 1
                        ELSE length(substr(path, instr(path, '/.Trash/') + 8)) END)
        ) AS n
        FROM files WHERE category='trash'
    """).fetchone()
    return [{
        "id": "empty_trash",
        "title": "Empty the Trash",
        "detail": f"{n_row['n']:,} item{'' if n_row['n'] == 1 else 's'} in ~/.Trash",
        "size": s_row["s"],
        "tier": 1,
        "action": "category:trash",
        "tone": "safe",
    }]


def _caches(conn):
    r = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS s FROM files WHERE category='cache'"
    ).fetchone()
    if not r["s"]:
        return []
    return [{
        "id": "clear_caches",
        "title": "Clear application caches",
        "detail": f"{r['n']:,} cached files (browsers, package managers, system)",
        "size": r["s"],
        "tier": 1,
        "action": "category:cache",
        "tone": "safe",
    }]


def _stale_artifacts(conn, now):
    cutoff = now - MONTH_6
    r = conn.execute("""
        SELECT COUNT(DISTINCT p.path) AS n,
               COALESCE(SUM(f.size), 0) AS s
        FROM projects p
        JOIN files f ON f.path LIKE p.path || '/%'
        WHERE p.last_activity < ?
          AND f.category = 'dev_artifact'
    """, (cutoff,)).fetchone()
    if not r["s"]:
        return []
    return [{
        "id": "stale_artifacts",
        "title": "Build artifacts in dormant projects",
        "detail": f"{r['n']} repos untouched 6+ months — node_modules, target, venv",
        "size": r["s"],
        "tier": 2,
        "action": "stale_artifacts",
        "tone": "review",
    }]


def _old_logs(conn, now):
    r = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS s FROM files WHERE category='logs' AND mtime < ?",
        (now - 90 * DAY,),
    ).fetchone()
    if not r["s"]:
        return []
    return [{
        "id": "old_logs",
        "title": "Log files older than 3 months",
        "detail": f"{r['n']:,} stale logs",
        "size": r["s"],
        "tier": 1,
        "action": "old_logs",
        "tone": "safe",
    }]


def _installers(conn):
    r = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS s FROM files WHERE category='installer'"
    ).fetchone()
    if not r["s"]:
        return []
    return [{
        "id": "installers",
        "title": "Old installers (.dmg / .pkg)",
        "detail": f"{r['n']} installer files — already installed?",
        "size": r["s"],
        "tier": 2,
        "action": "category:installer",
        "tone": "review",
    }]


def _old_downloads(conn, now):
    r = conn.execute("""
        SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS s FROM files
        WHERE category='downloads' AND atime < ?
    """, (now - 90 * DAY,)).fetchone()
    if not r["s"]:
        return []
    return [{
        "id": "old_downloads",
        "title": "Downloads not opened in 3+ months",
        "detail": f"{r['n']} files in ~/Downloads, untouched",
        "size": r["s"],
        "tier": 2,
        "action": "old_downloads",
        "tone": "review",
    }]


def _ios_backups(conn):
    r = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS s FROM files WHERE category='ios_backup'"
    ).fetchone()
    if not r["s"]:
        return []
    return [{
        "id": "ios_backups",
        "title": "iOS / iPad device backups",
        "detail": "Local backups in MobileSync — verify before removing",
        "size": r["s"],
        "tier": 2,
        "action": "category:ios_backup",
        "tone": "review",
    }]


def _forgotten(conn, now):
    r = conn.execute("""
        SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS s FROM files
        WHERE size > 100*1024*1024 AND atime < ?
          AND category NOT IN ('cache','dev_artifact','trash','logs')
    """, (now - YEAR,)).fetchone()
    if not r["s"]:
        return []
    return [{
        "id": "forgotten_large",
        "title": "Large files untouched 1+ year",
        "detail": f"{r['n']} files over 100 MB — videos, archives, models",
        "size": r["s"],
        "tier": 2,
        "action": "forgotten",
        "tone": "review",
    }]
