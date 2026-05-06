"""Cross-reference / redundancy detection.

Eight detector functions. Mostly rule-based (deterministic, $0). LLM is only
used for fuzzy types 7-8. Findings are persisted into the redundancies table.
"""
import os
import re
import time
from pathlib import Path

from . import db, native

HOME = str(Path.home())


def detect_all() -> dict:
    """Run every detector. Returns a summary count per type."""
    counts = {}
    with db.connect() as conn:
        conn.execute("DELETE FROM redundancies")
    counts["installer_dupes"] = _save("installer_dupes", _detect_installer_dupes())
    counts["tm_snapshots"]    = _save("tm_snapshots", _detect_tm_snapshots())
    counts["ios_backups"]     = _save("ios_backups", _detect_stale_ios_backups())
    counts["orphan_caches"]   = _save("orphan_caches", _detect_orphan_caches())
    counts["archive_pairs"]   = _save("archive_pairs", _detect_archive_pairs())
    counts["byte_dupes"]      = _save("byte_dupes", _detect_byte_dupes())
    counts["version_clusters"] = _save("version_clusters", _detect_version_clusters())
    return counts


def _save(type_: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    now = time.time()
    with db.connect() as conn:
        conn.executemany(
            """INSERT INTO redundancies
               (type, group_key, keep_path, redundant_path, size_freed,
                confidence, source, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(type_, r["group_key"], r.get("keep_path"), r["redundant_path"],
              r["size_freed"], r.get("confidence", 1.0), r.get("source", "rule"),
              r.get("detail"), now) for r in rows],
        )
    return len(rows)


# ── Type 1: Installer of an already-installed app ──────────────────────────
INSTALLER_VERSION_RE = re.compile(r"\s*v?\d+(\.\d+)*\.?\d*")


def _canonical_installer_name(name: str) -> str:
    """'RStudio-2025.05.0-496.dmg' → 'rstudio'"""
    stem = os.path.splitext(name)[0].lower()
    stem = INSTALLER_VERSION_RE.sub("", stem)
    return re.sub(r"[\s\-_]+", "", stem)


def _detect_installer_dupes() -> list[dict]:
    """An installer (.dmg/.pkg) is redundant if a matching app exists in apps_index."""
    with db.connect() as conn:
        installers = conn.execute(
            "SELECT path, name, size FROM files WHERE category='installer'"
        ).fetchall()
        apps = {a["display_name"].lower(): a["path"]
                for a in conn.execute("SELECT display_name, path FROM apps_index").fetchall()}

    findings = []
    for inst in installers:
        canon = _canonical_installer_name(inst["name"])
        # Try exact + prefix matches
        match = next((apps[name] for name in apps if name.replace(" ", "") == canon), None)
        if not match:
            match = next((apps[name] for name in apps if name.replace(" ", "").startswith(canon[:6]) and len(canon) >= 6), None)
        if match:
            findings.append({
                "group_key": canon,
                "keep_path": match,
                "redundant_path": inst["path"],
                "size_freed": inst["size"],
                "detail": f"App '{os.path.basename(match)}' is already installed.",
            })
    return findings


# ── Type 2: Time Machine local snapshots ───────────────────────────────────
def _detect_tm_snapshots() -> list[dict]:
    snaps = native.time_machine_local_snapshots()
    if not snaps:
        return []
    return [{
        "group_key": "tm_local_snapshots",
        "keep_path": None,
        "redundant_path": s["name"],
        "size_freed": 5 * 1024 * 1024 * 1024,  # rough estimate; tmutil doesn't size easily
        "detail": f"Local snapshot from {s['date']}",
    } for s in snaps]


# ── Type 3: Stale iOS device backups (>1 year since last backup) ───────────
def _detect_stale_ios_backups() -> list[dict]:
    backups = native.ios_backups(HOME)
    if not backups:
        return []
    cutoff = time.time() - 365 * 86_400
    findings = []
    for b in backups:
        last = b.get("last_backup")
        last_ts = last.timestamp() if hasattr(last, "timestamp") else 0
        if last_ts and last_ts < cutoff:
            # Get directory size from the file index
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(size), 0) AS s FROM files WHERE path LIKE ?",
                    (b["path"] + "/%",),
                ).fetchone()
                total = row["s"] if row else 0
            findings.append({
                "group_key": b["udid"],
                "keep_path": None,
                "redundant_path": b["path"],
                "size_freed": total,
                "detail": f"{b['device_name']} — last backed up {last.strftime('%Y-%m-%d') if hasattr(last, 'strftime') else 'long ago'}",
            })
    return findings


# ── Type 4: Orphan app caches (no matching installed app) ──────────────────
def _detect_orphan_caches() -> list[dict]:
    """Cache dirs whose bundle ID isn't in apps_index."""
    with db.connect() as conn:
        installed_bundles = {r["bundle_id"]
                              for r in conn.execute("SELECT bundle_id FROM apps_index").fetchall()}
        # Sum sizes per cache-dir basename inside Caches/
        rows = conn.execute("""
            SELECT
              substr(substr(path, instr(path, '/Library/Caches/') + 16),
                     1,
                     CASE WHEN instr(substr(path, instr(path, '/Library/Caches/') + 16), '/') > 0
                          THEN instr(substr(path, instr(path, '/Library/Caches/') + 16), '/') - 1
                          ELSE length(substr(path, instr(path, '/Library/Caches/') + 16)) END) AS bundle,
              SUM(size) AS total,
              MIN(path) AS sample_path
            FROM files
            WHERE category = 'cache' AND path LIKE '%/Library/Caches/%'
            GROUP BY bundle
        """).fetchall()
    findings = []
    for r in rows:
        bundle = r["bundle"]
        # Only check bundle-id-shaped names (e.g. com.foo.bar)
        if not bundle or bundle.count(".") < 2:
            continue
        if bundle in installed_bundles:
            continue
        if r["total"] < 10 * 1024 * 1024:  # ignore tiny orphans
            continue
        # Resolve cache dir root
        prefix_idx = r["sample_path"].find("/Library/Caches/") + len("/Library/Caches/")
        root = r["sample_path"][:prefix_idx + len(bundle)]
        findings.append({
            "group_key": bundle,
            "keep_path": None,
            "redundant_path": root,
            "size_freed": r["total"],
            "detail": f"Cache for '{bundle}' — app not installed.",
        })
    return findings


# ── Type 5: Archive + same-name folder pair ─────────────────────────────────
def _detect_archive_pairs() -> list[dict]:
    """`foo.zip` exists and a folder `foo/` exists in the same parent."""
    with db.connect() as conn:
        archives = conn.execute(
            """SELECT path, name, size FROM files
               WHERE category='documents' AND subcategory='archive'
                 AND size > 50*1024*1024"""
        ).fetchall()
    findings = []
    for a in archives:
        stem = os.path.splitext(a["name"])[0]
        # double-extension handling for .tar.gz etc.
        if stem.endswith(".tar"):
            stem = stem[:-4]
        sibling = os.path.join(os.path.dirname(a["path"]), stem)
        if os.path.isdir(sibling):
            findings.append({
                "group_key": a["path"],
                "keep_path": sibling,
                "redundant_path": a["path"],
                "size_freed": a["size"],
                "detail": f"Archive next to its extracted folder '{stem}'.",
            })
    return findings


# ── Type 6: Byte-identical large files (size + name match heuristic) ────────
def _detect_byte_dupes() -> list[dict]:
    """SQL-only first pass: same name, same size, different paths.
    Hashing left as a future enhancement (BLAKE3 first 1 MB).
    """
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT name, size, GROUP_CONCAT(path, '|') AS paths, COUNT(*) AS n
            FROM files
            WHERE size > 50*1024*1024 AND category != 'cache'
            GROUP BY name, size
            HAVING n > 1
            LIMIT 100
        """).fetchall()
    findings = []
    for r in rows:
        paths = r["paths"].split("|")
        # Keep the shortest path (often the most "canonical")
        keeper = min(paths, key=len)
        for p in paths:
            if p == keeper:
                continue
            findings.append({
                "group_key": f"{r['name']}|{r['size']}",
                "keep_path": keeper,
                "redundant_path": p,
                "size_freed": r["size"],
                "confidence": 0.85,
                "detail": f"Same name and size as {os.path.basename(keeper)}.",
            })
    return findings


# ── Type 7: Multi-version installers ───────────────────────────────────────
def _detect_version_clusters() -> list[dict]:
    """Cluster installers by canonical name. Keep the newest mtime, flag the rest."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT path, name, size, mtime FROM files WHERE category='installer' ORDER BY mtime DESC"
        ).fetchall()
    clusters = {}
    for r in rows:
        canon = _canonical_installer_name(r["name"])
        clusters.setdefault(canon, []).append(dict(r))
    findings = []
    for canon, items in clusters.items():
        if len(items) < 2:
            continue
        keeper = max(items, key=lambda x: x["mtime"])
        for it in items:
            if it["path"] == keeper["path"]:
                continue
            findings.append({
                "group_key": canon,
                "keep_path": keeper["path"],
                "redundant_path": it["path"],
                "size_freed": it["size"],
                "detail": f"Older version — newest is {os.path.basename(keeper['path'])}",
            })
    return findings


# ── Public API ─────────────────────────────────────────────────────────────
TYPE_LABELS = {
    "installer_dupes":   ("Installers of installed apps",      "App is already in /Applications — installer can go."),
    "tm_snapshots":      ("Time Machine local snapshots",      "Local snapshots that take disk space until they age out."),
    "ios_backups":       ("Stale iOS device backups",          "Devices not backed up in over a year."),
    "orphan_caches":     ("Orphan app caches",                 "Cache folders for apps no longer installed."),
    "archive_pairs":     ("Archive + extracted folder",        "ZIP next to its unzipped folder — one is redundant."),
    "byte_dupes":        ("Likely duplicate files",            "Same name and size in multiple locations."),
    "version_clusters":  ("Older installer versions",          "Multiple versions of the same installer."),
}


def grouped_findings() -> list[dict]:
    """Return findings grouped by type, ready for the frontend."""
    out = []
    with db.connect() as conn:
        for type_, (title, sub) in TYPE_LABELS.items():
            rows = conn.execute(
                """SELECT id, group_key, keep_path, redundant_path, size_freed, confidence, detail
                   FROM redundancies WHERE type = ? ORDER BY size_freed DESC""",
                (type_,),
            ).fetchall()
            if not rows:
                continue
            total_size = sum(r["size_freed"] for r in rows)
            out.append({
                "type": type_,
                "title": title,
                "subtitle": sub,
                "count": len(rows),
                "total_size": total_size,
                "items": [dict(r) for r in rows[:50]],
            })
    return sorted(out, key=lambda g: -g["total_size"])
