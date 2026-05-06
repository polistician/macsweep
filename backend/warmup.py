"""Pre-compute every expensive thing the UI needs on first paint.

After a scan completes, this runs in the background and writes ready-to-serve
JSON to the warmup_cache table. The frontend boot polls /api/warmup/status
and shows a real progress bar instead of waiting blindly.

Steps run in order of "user sees this first":
  1. overview          (Smart Scan hero stats, sidebar totals)
  2. suggestions       (Smart Scan list)
  3. redundancies      (sidebar badge + module)
  4. sunburst          (Storage Map)
  5. smart_picks       (Files > Smart tab)

State is in-memory + persisted to warmup_cache, so a fresh process can
serve from disk and re-warm in the background only if scan_id changed.
"""
import json
import time
import threading
from typing import Optional

from . import db


STATE = {
    "running": False,
    "step": None,        # current step name
    "progress": 0,       # 0-100
    "scan_id": None,
    "started_at": 0.0,
    "finished_at": 0.0,
    "error": None,
}


STEPS = [
    ("overview",     "Reading the index",      8),
    ("suggestions",  "Building Smart Scan",   12),
    ("redundancies", "Looking for duplicates",10),
    ("sunburst",     "Mapping the storage",   15),
    ("smart_picks",  "Ranking smart picks",    9),
]


def _put(key: str, value, scan_id: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO warmup_cache (key, value, scan_id, created_at) VALUES (?, ?, ?, ?)",
            (key, json.dumps(value), scan_id, time.time()),
        )


def _latest_done_scan_id() -> Optional[int]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id FROM scan_meta WHERE status='done' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None


def get(key: str, scoped: bool = False) -> Optional[dict | list]:
    """Read a cached value.

    scoped=False (default): return the freshest entry for this key, regardless
    of scan_id. Used for stable-shape data (overview, suggestions list) so the
    UI keeps showing data during the brief window between scan completion and
    warmup completion.

    scoped=True: only return the entry written for the *latest done scan*.
    Used for path-bearing caches (action_groups:*) where stale entries point
    to files that no longer exist.
    """
    with db.connect() as conn:
        if scoped:
            sid = _latest_done_scan_id()
            if sid is None:
                return None
            row = conn.execute(
                "SELECT value FROM warmup_cache WHERE key=? AND scan_id=?",
                (key, sid),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT value FROM warmup_cache WHERE key=? ORDER BY scan_id DESC LIMIT 1",
                (key,),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["value"])
        except Exception:
            return None


def is_warm() -> bool:
    """All expected keys cached for the latest done scan?"""
    sid = _latest_done_scan_id()
    if sid is None:
        return False
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT key FROM warmup_cache WHERE scan_id=?", (sid,)
        ).fetchall()
    cached = {r["key"] for r in rows}
    required = {s[0] for s in STEPS}
    return required.issubset(cached)


def status() -> dict:
    """Return current warmup state for the frontend splash."""
    return dict(STATE)


def _prune_stale(keep_scan_id: int) -> None:
    """Drop warmup_cache rows for scans older than keep_scan_id."""
    with db.connect() as conn:
        conn.execute(
            "DELETE FROM warmup_cache WHERE scan_id < ?", (keep_scan_id,)
        )


def invalidate(scan_id: Optional[int] = None) -> None:
    """Drop all warmup_cache rows for a scan so cached endpoints fall through
    to live computation. Use after mutations (quarantine, purge) that change
    the files table — otherwise the UI keeps showing pre-mutation snapshots."""
    sid = scan_id if scan_id is not None else _latest_done_scan_id()
    if sid is None:
        return
    with db.connect() as conn:
        conn.execute("DELETE FROM warmup_cache WHERE scan_id=?", (sid,))


def warm(scan_id: Optional[int] = None) -> None:
    """Run all warmup steps. Safe to call multiple times — uses cache when fresh."""
    if STATE["running"]:
        return

    sid = scan_id or _latest_done_scan_id()
    if sid is None:
        return

    STATE.update(running=True, step=None, progress=0, scan_id=sid,
                  started_at=time.time(), finished_at=0.0, error=None)

    # Imports inside fn to avoid circular at module load
    from . import grouping, redundancy, smart, suggestions

    total_weight = sum(w for _, _, w in STEPS)
    done_weight = 0

    def step_start(label: str):
        STATE["step"] = label
        STATE["progress"] = int((done_weight / total_weight) * 100)

    def step_end(weight: int):
        nonlocal done_weight
        done_weight += weight
        STATE["progress"] = int((done_weight / total_weight) * 100)

    try:
        from .main import _build_overview, _build_sunburst

        # 1. overview
        step_start("Reading the index")
        _put("overview", _build_overview(), sid)
        step_end(8)

        # 2. suggestions
        step_start("Building Smart Scan")
        sg = suggestions.generate()
        _put("suggestions", sg, sid)
        step_end(12)

        # 3. redundancies snapshot
        step_start("Looking for duplicates")
        _put("redundancies", redundancy.grouped_findings(), sid)
        step_end(10)

        # 4. sunburst
        step_start("Mapping the storage")
        _put("sunburst", _build_sunburst(depth=6), sid)
        step_end(15)

        # 5. smart picks
        step_start("Ranking smart picks")
        _put("smart_picks", smart.picks(limit=120, min_size_mb=10), sid)
        step_end(9)

        # 6. pre-compute group expansion for every suggestion so clicking is instant
        step_start("Pre-loading details")
        for s in (sg or []):
            action = s.get("action")
            if not action:
                continue
            try:
                groups = grouping.groups_for(action, limit=80)
                _put(f"action_groups:{action}", groups, sid)
            except Exception as e:
                print(f"[warmup] action {action} failed: {e}")

        # Drop rows from older scans — they're never read anymore.
        _prune_stale(sid)

        STATE["step"] = "ready"
        STATE["progress"] = 100
    except Exception as e:
        import traceback
        STATE["error"] = str(e)
        print(f"[warmup] failed: {e}")
        traceback.print_exc()
    finally:
        STATE["running"] = False
        STATE["finished_at"] = time.time()


def warm_async(scan_id: Optional[int] = None) -> None:
    """Fire-and-forget warmup in a daemon thread."""
    if STATE["running"]:
        return
    threading.Thread(target=warm, args=(scan_id,), daemon=True).start()
