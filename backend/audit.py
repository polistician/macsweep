"""Sweeper Audit — batched-prompt mass verdicts.

The "Ask Sweeper for top 30" button is a thin wrapper over per-file calls.
This module is for real bulk: 100–500 files at a time, with batched prompts
(20 files per LLM call → ~30× cheaper), pre-filtering via existing smart
heuristics, and a progress-tracked background job.

Pre-filter rationale:
    Most files don't need an LLM. Caches/build-artifacts/Trash are
    obviously safe; Documents/Library system stuff is obviously keep.
    `smart.picks()` already returns the most deletable candidates, so we
    use it as the input pool. The LLM only verdicts the ambiguous middle.

State machine:
    idle → running → done | error → idle (after results consumed)

Outputs:
    Verdicts persist on files.ai_verdict / ai_reason / ai_confidence /
    ai_verdict_at — same columns the per-file path uses. So results
    survive across runs and re-clicks are instant from the cache.
"""
import json
import logging
import os
import threading
import time
from typing import Optional

from . import db, integrator_chat, llm, llm_client, smart

log = logging.getLogger("macsweep.audit")


BATCH_SIZE = 20            # files per LLM call
MAX_FILES = 500            # hard cap per audit run — keeps runaway clicks bounded
MIN_SIZE_MB = 10           # skip noise

SYSTEM_PROMPT = """You evaluate Mac files for deletion safety in BULK.

Input: a JSON object with a "files" array. Each entry has metadata:
  index, path, name, size_mb, last_modified_days_ago, last_accessed_days_ago, category, kind, regenerable.
You do NOT see file contents.

Output JSON only — no prose, no fences:
{"verdicts":[
  {"index":0,"verdict":"safe"|"review"|"keep","confidence":0-100,"reason":"<one short sentence>"},
  ...
]}
The verdicts array MUST have one entry per input file, with matching `index`.

Verdicts:
- safe: regenerable junk (caches, build artifacts, package downloads), abandoned media touched once long ago, installer .dmg/.pkg, downloaded content stale 6+ months
- review: ambiguous — could be either, user judgement required
- keep: anything personal/important — financial, medical, legal, work, custom code, or recent (touched < 30 days)

Heuristics:
- "tax", "w2", "1099", "passport", "resume", "contract", "invoice", "wedding", "medical", "deed", "insurance" in name → keep, high confidence
- A year (2018–2025) in the name often signals personal context → lean keep
- /Caches/, /node_modules/, /target/, /build/, /.venv/, /DerivedData/ in path → safe, high confidence
- ~/Documents, ~/Desktop, ~/Pictures and not regenerable → keep unless obviously stale
- Confidence 90+ only when extremely obvious. Default 60–75.
- Reason: one sentence, plain English, second person.
"""


# Singleton audit state. Only one audit at a time per process.
_LOCK = threading.Lock()
_STATE: dict = {
    "phase": "idle",          # idle | running | done | error
    "started_at": 0,
    "finished_at": 0,
    "total": 0,
    "done": 0,
    "current_path": "",
    "scope": "",
    "error": "",
    "verdict_counts": {"safe": 0, "review": 0, "keep": 0},
}


def status() -> dict:
    """Snapshot of current audit progress. Always safe to call."""
    return {
        **_STATE,
        "elapsed": (time.time() - _STATE["started_at"]) if _STATE["started_at"] else 0,
        "ai_path": _ai_path_label(),
    }


def _ai_path_label() -> str:
    cfg = llm.load_config()
    if cfg.get("ai_verdicts_enabled") and integrator_chat.is_connected():
        return "integrator"
    return "direct"


def _file_payload(idx: int, row: dict) -> dict:
    now = time.time()
    days_mod = max(0, int((now - row["mtime"]) / 86400)) if row.get("mtime") else None
    days_acc = max(0, int((now - row["atime"]) / 86400)) if row.get("atime") else None
    return {
        "index": idx,
        "path": row["path"],
        "name": os.path.basename(row["path"]),
        "size_mb": round(row["size"] / (1024 * 1024), 2),
        "last_modified_days_ago": days_mod,
        "last_accessed_days_ago": days_acc,
        "category": row.get("category"),
        "kind": row.get("subcategory") or row.get("ext") or "",
        "regenerable": bool(row.get("regenerable")),
    }


def _call_llm_batch(files: list[dict]) -> Optional[list[dict]]:
    """Send a batch of files in ONE prompt, parse the verdicts array.
    Returns None on any failure (caller logs as error per file)."""
    cfg = llm.load_config()
    user_text = json.dumps({"files": files})
    use_integrator = cfg.get("ai_verdicts_enabled") and integrator_chat.is_connected()
    try:
        if use_integrator:
            parsed = integrator_chat.chat_json(
                system=SYSTEM_PROMPT, user=user_text,
                max_tokens=2000, temperature=0.2,
            )
        else:
            parsed = llm_client.chat_json(
                system=SYSTEM_PROMPT, user=user_text,
                max_tokens=2000, temperature=0.2,
            )
    except integrator_chat.IntegratorError as e:
        log.warning("audit batch: integrator failed: %s", e)
        return None
    if not parsed or not isinstance(parsed, dict):
        return None
    arr = parsed.get("verdicts")
    if not isinstance(arr, list):
        return None
    return arr


def _persist(path: str, verdict: str, confidence: int, reason: str) -> None:
    try:
        with db.connect() as conn:
            conn.execute(
                "UPDATE files SET ai_verdict=?, ai_reason=?, ai_confidence=?, ai_verdict_at=? "
                "WHERE path=?",
                (verdict, reason, confidence, int(time.time()), path),
            )
    except Exception as e:
        log.warning("audit persist failed for %s: %s", path, e)


def _run(scope: str, max_files: int, min_size_mb: int) -> None:
    """Main loop, runs in a background thread."""
    try:
        # 1. Pre-filter via smart.picks — already excludes obvious system stuff.
        candidates = smart.picks(limit=max_files, min_size_mb=min_size_mb)
        # 2. Skip files that already have a recent cached verdict so we don't
        #    burn LLM tokens re-asking. Re-asks only happen when the user
        #    explicitly forces refresh from the UI.
        with db.connect() as conn:
            paths = [c["path"] for c in candidates]
            if paths:
                placeholders = ",".join("?" for _ in paths)
                cached_rows = conn.execute(
                    f"SELECT path, ai_verdict FROM files "
                    f"WHERE path IN ({placeholders}) AND ai_verdict IS NOT NULL",
                    paths,
                ).fetchall()
                cached_paths = {r["path"] for r in cached_rows}
            else:
                cached_paths = set()
        fresh = [c for c in candidates if c["path"] not in cached_paths]

        with _LOCK:
            _STATE["total"] = len(fresh)
            _STATE["done"] = 0
            _STATE["scope"] = scope
            _STATE["verdict_counts"] = {"safe": 0, "review": 0, "keep": 0}

        # 3. Batch + LLM call
        for batch_start in range(0, len(fresh), BATCH_SIZE):
            batch = fresh[batch_start: batch_start + BATCH_SIZE]
            payload = [_file_payload(i, row) for i, row in enumerate(batch)]
            with _LOCK:
                _STATE["current_path"] = batch[0]["path"] if batch else ""
            verdicts = _call_llm_batch(payload)
            if verdicts is None:
                # Whole batch failed — log and continue. Per-file path will
                # still work later for these.
                log.warning("audit: batch starting at %d returned no verdicts", batch_start)
                with _LOCK:
                    _STATE["done"] += len(batch)
                continue
            # Map verdicts back to files by `index`.
            by_idx = {v.get("index"): v for v in verdicts if isinstance(v, dict)}
            for i, row in enumerate(batch):
                v = by_idx.get(i)
                if not v:
                    continue
                vd = v.get("verdict")
                if vd not in ("safe", "review", "keep"):
                    continue
                try:
                    conf = max(0, min(100, int(v.get("confidence", 0))))
                except (TypeError, ValueError):
                    conf = 0
                reason = str(v.get("reason") or "").strip()[:280]
                _persist(row["path"], vd, conf, reason)
                with _LOCK:
                    _STATE["verdict_counts"][vd] = _STATE["verdict_counts"].get(vd, 0) + 1
            with _LOCK:
                _STATE["done"] += len(batch)

        with _LOCK:
            _STATE["phase"] = "done"
            _STATE["finished_at"] = time.time()
            _STATE["current_path"] = ""
    except Exception as e:
        import traceback
        traceback.print_exc()
        with _LOCK:
            _STATE["phase"] = "error"
            _STATE["error"] = str(e)
            _STATE["finished_at"] = time.time()


def start(scope: str = "smart_picks", max_files: int = 100, min_size_mb: int = 10) -> dict:
    """Kick off an audit. Refuses if one is already running. Caps `max_files`
    to MAX_FILES regardless of input."""
    with _LOCK:
        if _STATE["phase"] == "running":
            return {"ok": False, "error": "An audit is already running."}
        max_files = max(1, min(int(max_files), MAX_FILES))
        _STATE.update(
            phase="running",
            started_at=time.time(),
            finished_at=0,
            total=0, done=0,
            current_path="", scope=scope, error="",
            verdict_counts={"safe": 0, "review": 0, "keep": 0},
        )
    threading.Thread(
        target=_run, args=(scope, max_files, min_size_mb), daemon=True,
    ).start()
    return {"ok": True, "phase": "running"}


def reset() -> None:
    """Clear the state machine back to idle. Doesn't touch persisted verdicts."""
    with _LOCK:
        _STATE.update(phase="idle", current_path="", error="")


def results(min_confidence: int = 0,
            verdicts: Optional[list[str]] = None,
            limit: int = 500) -> list[dict]:
    """Return audit-cached verdicts joined with file metadata, filtered by
    confidence + verdict types. Sorted by confidence DESC then size DESC.
    """
    if verdicts:
        verdicts = [v for v in verdicts if v in ("safe", "review", "keep")]
    if not verdicts:
        verdicts = ["safe", "review", "keep"]

    placeholders = ",".join("?" for _ in verdicts)
    sql = (
        f"SELECT path, name, size, mtime, atime, category, subcategory, "
        f"       ai_verdict, ai_reason, ai_confidence, ai_verdict_at "
        f"FROM files "
        f"WHERE ai_verdict IN ({placeholders}) "
        f"  AND COALESCE(ai_confidence, 0) >= ? "
        f"ORDER BY ai_confidence DESC, size DESC "
        f"LIMIT ?"
    )
    with db.connect() as conn:
        rows = conn.execute(sql, [*verdicts, int(min_confidence), int(limit)]).fetchall()
    return [dict(r) for r in rows]
