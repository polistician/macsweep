"""Aggregate file lists into meaningful groups for the cleanup drawer.

The user wants to think in terms of "Brave is using 12 GB of cache" — not 23,000
file paths. Each suggestion type has its own grouping rule that aggregates files
to the boundary that matters: app for caches, trashed item for the Trash, project
for build artifacts.
"""
import os
import time
from pathlib import Path

from . import db

HOME = str(Path.home())


def groups_for(slug, limit=80):
    """Return a list of groups for a given action slug.

    Each group: { name, path, size, files, regenerable, last_touched, samples }
    """
    rows = _files_for(slug)
    keyfn = _grouper(slug)

    bucket = {}
    for r in rows:
        key, root = keyfn(r["path"])
        if key is None:
            continue
        g = bucket.setdefault(key, {
            "name": key, "path": root, "size": 0, "files": 0,
            "regenerable": True, "last_touched": 0.0, "samples": [],
        })
        g["size"] += r["size"]
        g["files"] += 1
        if r["mtime"] > g["last_touched"]:
            g["last_touched"] = r["mtime"]
        if not r["regenerable"]:
            g["regenerable"] = False
        if len(g["samples"]) < 3:
            g["samples"].append(r["path"])

    # Sort + slice to `limit` BEFORE label resolution. For categories with
    # tens of thousands of single-file "groups" (e.g. Downloads), labelling
    # every one was costing ~25s — labels are only ever shown for the top N.
    out = sorted(bucket.values(), key=lambda g: -g["size"])[:limit]

    from . import labels as _labels
    for g in out:
        g["kind"] = _group_kind(g["path"], g["samples"])
        try:
            label = _labels.resolve_label(g["path"])
            g["display_name"] = label["name"]
            g["reason"] = label["reason"]
            g["label_source"] = label["source"]
            g["raw_key"] = g["name"]
            if label["name"] != "Unknown":
                g["name"] = label["name"]
        except Exception:
            g["display_name"] = g["name"]
            g["reason"] = ""
            g["label_source"] = "fallback"

    return out


def _group_kind(root_path, samples):
    import os
    ext = os.path.splitext(root_path)[1].lower()
    if root_path.endswith(".app"):
        return "app"
    if ext:
        return _kind_from_ext(ext)
    # Folder-shaped group — guess from contents
    for s in samples:
        e = os.path.splitext(s)[1].lower()
        if e:
            return _kind_from_ext(e)
    return "dir"


def _kind_from_ext(ext):
    ext = ext.lstrip(".")
    if ext in {"jpg","jpeg","png","gif","heic","tiff","webp","bmp","svg","raw"}: return "image"
    if ext in {"mp4","mov","mkv","avi","webm","m4v"}: return "video"
    if ext in {"mp3","m4a","wav","flac","aac","ogg"}: return "audio"
    if ext == "pdf": return "pdf"
    if ext in {"zip","tar","gz","tgz","rar","7z","bz2","xz"}: return "archive"
    if ext in {"dmg","pkg","iso"}: return "installer"
    if ext in {"doc","docx","pages","key","numbers","xls","xlsx","ppt","pptx"}: return "document"
    if ext in {"txt","md","csv","log","json","xml","yaml","yml","html","css"}: return "text"
    if ext in {"py","js","ts","tsx","jsx","go","rs","java","c","cpp","h","sh","rb","swift"}: return "code"
    if ext in {"gguf","safetensors","ckpt","pt","pth","onnx","bin"}: return "model"
    return "other"


def _files_for(slug):
    """Return raw rows matching a suggestion's action."""
    now = time.time()
    with db.connect() as conn:
        if slug.startswith("category:"):
            cat = slug.split(":", 1)[1]
            return conn.execute(
                "SELECT path, size, mtime, regenerable FROM files WHERE category=? ORDER BY size DESC",
                (cat,),
            ).fetchall()
        if slug == "stale_artifacts":
            cutoff = now - 180 * 86_400
            return conn.execute("""
                SELECT f.path, f.size, f.mtime, f.regenerable
                FROM files f
                JOIN projects p ON f.path LIKE p.path || '/%'
                WHERE p.last_activity < ? AND f.category='dev_artifact'
                ORDER BY f.size DESC
            """, (cutoff,)).fetchall()
        if slug == "old_logs":
            cutoff = now - 90 * 86_400
            return conn.execute(
                "SELECT path, size, mtime, regenerable FROM files WHERE category='logs' AND mtime < ? ORDER BY size DESC",
                (cutoff,),
            ).fetchall()
        if slug == "old_downloads":
            cutoff = now - 90 * 86_400
            return conn.execute(
                "SELECT path, size, mtime, regenerable FROM files WHERE category='downloads' AND atime < ? ORDER BY size DESC",
                (cutoff,),
            ).fetchall()
        if slug == "forgotten":
            cutoff = now - 365 * 86_400
            return conn.execute("""
                SELECT path, size, mtime, regenerable FROM files
                WHERE size > 100*1024*1024 AND atime < ?
                  AND category NOT IN ('cache','dev_artifact','trash','logs')
                ORDER BY size DESC
            """, (cutoff,)).fetchall()
        return []


def _grouper(slug):
    """Return a function (path) -> (group_key, group_root_path)."""
    if slug == "category:cache":
        return _cache_group
    if slug == "category:trash":
        return _trash_group
    if slug in ("old_logs", "category:logs"):
        return _logs_group
    if slug == "stale_artifacts":
        return _project_group
    if slug == "category:ios_backup":
        return _ios_group
    # Per-file: each row is its own pickable item (Downloads-style). Use this
    # for any category where the user thinks in terms of individual files
    # rather than directories — otherwise Media collapses into one giant
    # "Movies" bucket and the user can only select all-or-nothing.
    if slug in (
        "old_downloads", "category:downloads",
        "category:installer", "category:media", "category:documents",
        "category:ml_model", "category:apps", "category:other",
        "category:dev_artifact",
        "forgotten",
    ):
        return _file_group
    # default: top-level home dir
    return _home_group


def _split_after(path, marker):
    """Return (segment_after_marker, root_path_through_segment) or (None, None)."""
    idx = path.find(marker)
    if idx < 0:
        return None, None
    rest = path[idx + len(marker):]
    seg, *_ = rest.split("/", 1)
    if not seg:
        return None, None
    root = path[:idx + len(marker)] + seg
    return seg, root


def _cache_group(path):
    # Tool caches — collapse the whole directory tree into one group.
    if "/.npm/" in path:
        return "npm", f"{HOME}/.npm"
    if "/.cargo/registry/" in path:
        return "Cargo", f"{HOME}/.cargo/registry"
    if "/go/pkg/mod/cache/" in path:
        return "Go modules", f"{HOME}/go/pkg/mod/cache"
    if "/.yarn/cache/" in path:
        return "Yarn", f"{HOME}/.yarn/cache"
    # Generic: split after the cache root marker, use the immediate child as the app name.
    for marker in (
        "/Library/Caches/",
        "/Library/Application Support/",
        "/.cache/",
    ):
        seg, root = _split_after(path, marker)
        if seg:
            return _humanize_cache_name(seg), root
    return _home_group(path)


_PRETTY_CACHE = {
    "Google": "Google Chrome",
    "BraveSoftware": "Brave Browser",
    "com.apple.Safari": "Safari",
    "Firefox": "Firefox",
    "com.microsoft.VSCode": "Visual Studio Code",
    "Cursor": "Cursor",
    "Slack": "Slack",
    "discord": "Discord",
    "Spotify": "Spotify",
    "com.spotify.client": "Spotify",
    "com.tinyspeck.slackmacgap": "Slack",
    "Homebrew": "Homebrew",
    "Yarn": "Yarn",
    "pip": "pip",
    "_cacache": "npm",
}


def _humanize_cache_name(seg):
    return _PRETTY_CACHE.get(seg, seg)


def _trash_group(path):
    # ~/.Trash/<item>... → group by direct child of Trash
    return _split_after(path, "/.Trash/")


def _logs_group(path):
    # ~/Library/Logs/<source>/...
    return _split_after(path, "/Library/Logs/")


def _project_group(path):
    # Find which project this file belongs to and use that as the group.
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT path, name FROM projects WHERE ? LIKE path || '/%' ORDER BY length(path) DESC LIMIT 1",
            (path,),
        ).fetchall()
    if rows:
        return rows[0]["name"], rows[0]["path"]
    return None, None


def _ios_group(path):
    # ~/Library/Application Support/MobileSync/Backup/<UUID>/...
    seg, root = _split_after(path, "/MobileSync/Backup/")
    if seg:
        return f"iOS backup ({seg[:8]}…)", root
    return "iOS backup", path


def _file_group(path):
    # Each file is its own group — group_key = filename, root = full path
    return os.path.basename(path), path


def _home_group(path):
    if path.startswith(HOME):
        rest = path[len(HOME):].lstrip("/")
        seg = rest.split("/", 1)[0]
        return seg, f"{HOME}/{seg}"
    return None, None


def files_under(group_root):
    """List all files under a group's root path. Used when committing."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT path FROM files WHERE path = ? OR path LIKE ? ORDER BY size DESC",
            (group_root, group_root.rstrip("/") + "/%"),
        ).fetchall()
        return [r["path"] for r in rows]
