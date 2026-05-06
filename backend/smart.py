"""Smart Picks — predictive ranking of "most likely to delete" files.

The score combines four signals: size (log scale), idle months since last opened,
file-type tendency (installers / archives / videos rank high), and whether
the file is regenerable. Mirrors the same formula on the frontend so the visible
badge matches the server-side ranking.
"""
import math
import os
import time

from . import db

KIND_BOOST = {
    ".dmg": 18, ".pkg": 18, ".iso": 18,
    ".zip": 12, ".tar": 12, ".gz": 12, ".tgz": 12, ".7z": 12, ".rar": 12,
    ".mp4": 8, ".mov": 8, ".mkv": 8, ".avi": 8, ".webm": 8,
    ".jpg": 4, ".jpeg": 4, ".png": 4, ".heic": 4, ".tiff": 4,
    ".gguf": 6, ".safetensors": 6, ".ckpt": 6, ".pt": 6, ".pth": 6, ".onnx": 6,
}


def _score(size: int, atime: float, ext: str, regenerable: int, category: str) -> int:
    size_mb = (size or 0) / (1024 * 1024)
    idle_months = max(0, (time.time() - (atime or 0)) / (30 * 86_400))
    s = 0.0
    s += min(40, math.log10(size_mb + 1) * 12)
    s += min(35, idle_months * 2)
    s += KIND_BOOST.get(ext.lower() if ext else "", 0)
    if regenerable:
        s += 15
    if category in ("cache", "dev_artifact", "trash"):
        s += 15
    return min(100, round(s))


def _reason(size, atime, kind, regenerable, category):
    parts = []
    if size and size > 1024 * 1024 * 1024:
        parts.append(f"big ({size/(1024**3):.1f} GB)")
    if atime:
        idle_d = (time.time() - atime) / 86_400
        if idle_d > 365:
            parts.append(f"idle {idle_d/365:.1f}y")
        elif idle_d > 90:
            parts.append(f"idle {int(idle_d/30)}mo")
    if regenerable:
        parts.append("regenerable")
    if category == "cache":
        parts.append("cache")
    elif category == "trash":
        parts.append("in trash")
    elif category == "dev_artifact":
        parts.append("build artifact")
    return " · ".join(parts) or "low impact"


def picks(limit: int = 100, min_size_mb: int = 10) -> list[dict]:
    """Return files ranked by deletability score, descending."""
    min_bytes = min_size_mb * 1024 * 1024
    with db.connect() as conn:
        # Pull a pool of candidates, score in Python, sort.
        rows = conn.execute("""
            SELECT path, size, mtime, atime, category, subcategory, regenerable, ext
            FROM files
            WHERE size >= ?
            ORDER BY size DESC
            LIMIT 5000
        """, (min_bytes,)).fetchall()

    scored = []
    for r in rows:
        kind_ext = r["ext"] or os.path.splitext(r["path"])[1]
        s = _score(r["size"], r["atime"], kind_ext, r["regenerable"], r["category"])
        scored.append({
            **dict(r),
            "score": s,
            "reason": _reason(r["size"], r["atime"], kind_ext,
                              r["regenerable"], r["category"]),
        })
    scored.sort(key=lambda x: -x["score"])
    return scored[:limit]
