"""On-demand "should I delete this file?" verdict.

Sends only filename / path / size / timestamps / category to the model — never
file contents. Returns {verdict, confidence, reason}.

Two LLM paths, picked by config:
  - Integrator (preferred when ai_verdicts_enabled=True AND paired) — one
    central pairing covers every polistician.ai app.
  - llm_client (legacy) — direct OpenAI api_key or ChatGPT OAuth.
"""
import json
import logging
import os
import time
from typing import Optional

from . import db, integrator_chat, llm, llm_client

log = logging.getLogger("macsweep.file_verdict")


SYSTEM_PROMPT = """You evaluate whether one Mac file is safe to delete.

You receive ONLY metadata: path, name, size, last-modified date, last-accessed date, category, kind.
You do not see file contents.

Output JSON only — no prose, no fences:
{"verdict":"safe"|"review"|"keep","confidence":0-100,"reason":"<one short sentence>"}

Verdicts:
- safe: regenerable junk (caches, build artifacts, package downloads), abandoned media touched once long ago, installer .dmg/.pkg files, downloaded content stale 6+ months
- review: ambiguous — could be either, user judgment required
- keep: anything personal/important — financial, medical, legal, work, custom code, or a recent file (touched in last 30 days)

Heuristics:
- "tax", "w2", "1099", "passport", "resume", "contract", "invoice", "wedding", "medical", "deed", "insurance" in name → keep, high confidence
- A year (2018–2025) in the name often signals personal context → lean keep
- Path contains /Caches/, /node_modules/, /target/, /build/, /.venv/, /DerivedData/ → safe, high confidence
- Path is in ~/Documents, ~/Desktop, ~/Pictures and not regenerable → keep unless obviously stale
- Confidence 90+ only when extremely obvious. Default to 60-75.
- Reason: one sentence, plain English, second person ("You haven't…", "This is…").
"""


def _file_facts(path: str) -> Optional[dict]:
    """Look up size/mtime/atime/category from the index, fall back to lstat
    so we can still verdict files that weren't scanned."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT path, size, mtime, atime, category, subcategory, ext, regenerable "
            "FROM files WHERE path=?",
            (path,),
        ).fetchone()
    if row:
        return dict(row)
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return {
        "path": path,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "atime": st.st_atime,
        "category": None,
        "subcategory": None,
        "ext": os.path.splitext(path)[1].lstrip("."),
        "regenerable": 0,
    }


def _payload(facts: dict) -> dict:
    now = time.time()
    days_mod = max(0, int((now - facts["mtime"]) / 86400)) if facts.get("mtime") else None
    days_acc = max(0, int((now - facts["atime"]) / 86400)) if facts.get("atime") else None
    return {
        "path": facts["path"],
        "name": os.path.basename(facts["path"]),
        "size_mb": round(facts["size"] / (1024 * 1024), 2),
        "last_modified_days_ago": days_mod,
        "last_accessed_days_ago": days_acc,
        "category": facts.get("category"),
        "kind": facts.get("subcategory") or facts.get("ext") or "",
        "regenerable": bool(facts.get("regenerable")),
    }


def _cached_verdict(path: str) -> Optional[dict]:
    """Look up a previously-cached verdict for this exact path. Returns the
    same {ok, verdict, confidence, reason, cached:True} envelope or None."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT ai_verdict, ai_reason, ai_confidence, ai_verdict_at "
            "FROM files WHERE path=?",
            (path,),
        ).fetchone()
    if not row or not row["ai_verdict"]:
        return None
    return {
        "ok": True,
        "verdict": row["ai_verdict"],
        "confidence": row["ai_confidence"] or 0,
        "reason": row["ai_reason"] or "",
        "cached": True,
        "verdict_at": row["ai_verdict_at"],
    }


def _save_verdict(path: str, verdict: str, confidence: int, reason: str) -> None:
    """Persist the verdict on the files row. Silently no-op if the row
    doesn't exist (file was scanned but later removed, or never indexed)."""
    try:
        with db.connect() as conn:
            conn.execute(
                "UPDATE files SET ai_verdict=?, ai_reason=?, ai_confidence=?, ai_verdict_at=? "
                "WHERE path=?",
                (verdict, reason, confidence, int(time.time()), path),
            )
    except Exception as e:
        log.warning("could not cache verdict for %s: %s", path, e)


def _ai_path_label() -> str:
    """Which LLM path will fire when evaluate() runs. Used in error messages."""
    cfg = llm.load_config()
    if cfg.get("ai_verdicts_enabled") and integrator_chat.is_connected():
        return "integrator"
    return "direct"


def _call_llm(payload: dict) -> Optional[dict]:
    """Pick the LLM client based on config. Integrator wins when paired AND
    the user opted in via ai_verdicts_enabled. Falls back to llm_client.
    Raises nothing — returns None on any failure."""
    cfg = llm.load_config()
    user_text = json.dumps(payload)
    if cfg.get("ai_verdicts_enabled") and integrator_chat.is_connected():
        try:
            return integrator_chat.chat_json(
                system=SYSTEM_PROMPT,
                user=user_text,
                max_tokens=140,
                temperature=0.2,
            )
        except integrator_chat.IntegratorError as e:
            log.warning("integrator chat_json failed: %s", e)
            return None
    return llm_client.chat_json(
        system=SYSTEM_PROMPT,
        user=user_text,
        max_tokens=140,
        temperature=0.2,
    )


def evaluate(path: str, *, force: bool = False) -> dict:
    """Return {ok, verdict, confidence, reason} or {ok:False, error}.

    `force=True` bypasses the per-file cache (re-asks the LLM)."""
    if not force:
        cached = _cached_verdict(path)
        if cached is not None:
            return cached
    facts = _file_facts(path)
    if not facts:
        return {"ok": False, "error": "File not found"}
    parsed = _call_llm(_payload(facts))
    if not parsed:
        path_label = _ai_path_label()
        msg = (
            "Integrator unavailable — reconnect in Settings."
            if path_label == "integrator"
            else "LLM unavailable — check sign-in or API key in Settings"
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
    _save_verdict(path, verdict, confidence, reason)
    return {
        "ok": True,
        "verdict": verdict,
        "confidence": confidence,
        "reason": reason,
        "cached": False,
    }
