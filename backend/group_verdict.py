"""Group-level Sweeper verdict — "should I clear this whole folder?"

Samples up to 10 representative files under a group root, aggregates
size/age/kind statistics, and asks the LLM for a single verdict + reason
on the *group* as a whole.

Reuses the same routing as file_verdict (Integrator if enabled, else
llm_client). Cached per-path-prefix in-memory for the life of the process
to keep re-clicks instant without growing the SQLite schema.
"""
import json
import logging
import os
import time
from typing import Optional

from . import db, integrator_chat, llm, llm_client

log = logging.getLogger("macsweep.group_verdict")

SYSTEM_PROMPT = """You evaluate whether a folder of related Mac files is safe to delete AS A GROUP.

You receive aggregate metadata: the group root path, file count, total size,
age statistics, the dominant file kinds, and a sample of filenames inside.
You do not see file contents.

Output JSON only — no prose, no fences:
{"verdict":"safe"|"review"|"keep","confidence":0-100,"reason":"<one short sentence>"}

Verdicts:
- safe: regenerable junk in aggregate (caches, build artifacts, package downloads, abandoned media folder untouched 6+ months, installer downloads).
- review: mixed contents — user should expand and decide per-file.
- keep: contains personal/work/financial/medical/legal items, recent activity, or anything irreplaceable.

Heuristics:
- Path under /Caches/, /node_modules/, /target/, /build/, /.venv/, /DerivedData/, /Trash/ → safe, high confidence
- Path contains "tax", "resume", "contract", "medical", "passport", "wedding", "financial" → keep, high confidence
- Most recent activity within last 30 days AND under ~/Documents or ~/Desktop → keep
- Confidence 90+ only when extremely obvious. Default 60-75.
- Reason: one sentence, plain English, second person ("This whole folder is…", "You haven't touched…").
"""


_CACHE: dict[str, dict] = {}     # absolute_path → verdict envelope
_CACHE_TTL_S = 24 * 3600         # 1 day; trivial cache


def _range_bounds(path: str) -> tuple[str, str]:
    prefix = path.rstrip("/") + "/"
    return prefix, prefix[:-1] + chr(ord("/") + 1)


def _aggregate(path: str) -> Optional[dict]:
    """Build an aggregate payload for a path. Path may be a file or directory.
    Returns None if the path has no indexed files at all."""
    lo, hi = _range_bounds(path)
    with db.connect() as conn:
        agg = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS s, "
            "       MAX(mtime) AS newest, MIN(mtime) AS oldest, MAX(atime) AS last_acc "
            "FROM files WHERE path = ? OR (path >= ? AND path < ?)",
            (path, lo, hi),
        ).fetchone()
        if not agg or agg["n"] == 0:
            return None
        # Top 5 kinds by total size (ext or subcategory)
        kinds = conn.execute(
            "SELECT COALESCE(NULLIF(subcategory,''), NULLIF(ext,''), 'other') AS k, "
            "       COALESCE(SUM(size),0) AS s, COUNT(*) AS n "
            "FROM files WHERE path = ? OR (path >= ? AND path < ?) "
            "GROUP BY k ORDER BY s DESC LIMIT 5",
            (path, lo, hi),
        ).fetchall()
        # 8 sample filenames — biggest first, capped to keep the prompt small
        samples = conn.execute(
            "SELECT name FROM files WHERE path = ? OR (path >= ? AND path < ?) "
            "ORDER BY size DESC LIMIT 8",
            (path, lo, hi),
        ).fetchall()
        # Dominant category (for context)
        cat = conn.execute(
            "SELECT category, COUNT(*) AS n FROM files "
            "WHERE path = ? OR (path >= ? AND path < ?) "
            "GROUP BY category ORDER BY n DESC LIMIT 1",
            (path, lo, hi),
        ).fetchone()

    now = time.time()
    days_since_touch = (
        max(0, int((now - agg["last_acc"]) / 86400))
        if agg["last_acc"] else None
    )
    days_since_modified = (
        max(0, int((now - agg["newest"]) / 86400))
        if agg["newest"] else None
    )
    return {
        "path": path,
        "name": os.path.basename(path) or path,
        "file_count": agg["n"],
        "total_size_mb": round(agg["s"] / (1024 * 1024), 2),
        "last_accessed_days_ago": days_since_touch,
        "last_modified_days_ago": days_since_modified,
        "dominant_category": cat["category"] if cat else None,
        "top_kinds": [
            {"kind": k["k"], "files": k["n"], "size_mb": round(k["s"] / (1024 * 1024), 2)}
            for k in kinds
        ],
        "sample_names": [s["name"] for s in samples],
    }


def _call_llm(payload: dict) -> Optional[dict]:
    cfg = llm.load_config()
    user_text = json.dumps(payload)
    if cfg.get("ai_verdicts_enabled") and integrator_chat.is_connected():
        try:
            return integrator_chat.chat_json(
                system=SYSTEM_PROMPT, user=user_text,
                max_tokens=160, temperature=0.2,
            )
        except integrator_chat.IntegratorError as e:
            log.warning("integrator group verdict failed: %s", e)
            return None
    return llm_client.chat_json(
        system=SYSTEM_PROMPT, user=user_text,
        max_tokens=160, temperature=0.2,
    )


def evaluate(path: str, *, force: bool = False) -> dict:
    """Return {ok, verdict, confidence, reason, file_count, total_size_mb}
    for the group rooted at `path`. force=True bypasses the in-memory cache."""
    if not force:
        hit = _CACHE.get(path)
        if hit and (time.time() - hit.get("_at", 0)) < _CACHE_TTL_S:
            return {**hit, "cached": True}
    agg = _aggregate(path)
    if not agg:
        return {"ok": False, "error": "No indexed files under this path."}
    parsed = _call_llm(agg)
    if not parsed:
        cfg = llm.load_config()
        msg = (
            "Integrator unavailable — reconnect in Settings."
            if cfg.get("ai_verdicts_enabled") and integrator_chat.is_connected()
            else "LLM unavailable — check Settings."
        )
        return {"ok": False, "error": msg}
    verdict = parsed.get("verdict")
    if verdict not in ("safe", "review", "keep"):
        return {"ok": False, "error": "Model returned an unexpected verdict"}
    try:
        confidence = max(0, min(100, int(parsed.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0
    reason = str(parsed.get("reason") or "").strip()[:280]
    envelope = {
        "ok": True,
        "verdict": verdict,
        "confidence": confidence,
        "reason": reason,
        "file_count": agg["file_count"],
        "total_size_mb": agg["total_size_mb"],
        "_at": time.time(),
        "cached": False,
    }
    _CACHE[path] = envelope
    return envelope
