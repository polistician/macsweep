"""MacSweep HTTP API + static frontend."""
import threading
import time
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from . import audit, db, file_detail, file_verdict, group_verdict, grouping, integrator_chat, labels as labels_mod, llm, oauth_chatgpt, projects, quarantine, redundancy, scanner, smart, suggestions, sweep, updater, warmup

app = FastAPI(title="MacSweep")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


# Force fresh assets on every request — WKWebView caches static content
# aggressively and we don't have any way to invalidate it from the wrapper.
class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


app.add_middleware(NoCacheMiddleware)

db.init()
quarantine.auto_purge()

# On boot, mark ANY 'running' scan_meta rows as 'killed' — they're from a
# previous process that died. The current process's scans are tracked in
# scanner.STATE; we don't share running state across processes.
with db.connect() as conn:
    conn.execute("UPDATE scan_meta SET status='killed' WHERE status='running'")
# `_full_scan` is defined later in this file; the boot trigger fires after
# its definition.


# ── Models ────────────────────────────────────────────────────────────────────
class QuarantineRequest(BaseModel):
    paths: List[str]


# ── Scan ──────────────────────────────────────────────────────────────────────
def _boot_warmup_or_rescan():
    """On launch: only warm the cache if cold. Do NOT auto-rescan — that's
    been a source of "always have to rescan" frustration. If the index is
    stale (version bump), the freshness chip will tell the user; they
    explicitly click Rescan when they want fresh data.
    """
    if scanner.latest_index_version() is None:
        return
    if not warmup.is_warm():
        warmup.warm_async()


_FULL_SCAN_LOCK = threading.Lock()


def _full_scan():
    """Run scan + post-scan steps as one atomic pipeline. Lock prevents the
    TOCTOU race where two threads/processes start scans concurrently."""
    if not _FULL_SCAN_LOCK.acquire(blocking=False):
        return  # another scan is already in progress in this process
    meta_id = None
    try:
        meta_id = scanner.scan()
        if meta_id is None:
            return  # scan() refused (already-running guard)
        projects.detect_from_index()

        # Stay in 'warming' state for UI continuity while post-scan steps run.
        scanner.STATE["running"] = True
        scanner.STATE["phase"] = "warming"
        scanner.STATE["current"] = "Optimizing…"
        from . import labels as _labels, redundancy as _redundancy
        _labels.warm_apps_index()
        _redundancy.detect_all()
        warmup.warm()

        with db.connect() as conn:
            conn.execute("UPDATE scan_meta SET status='done' WHERE id=?", (meta_id,))
    except Exception as e:
        import traceback
        traceback.print_exc()
        scanner.STATE["error"] = str(e)
        if meta_id:
            try:
                with db.connect() as conn:
                    conn.execute("UPDATE scan_meta SET status='error', error=? WHERE id=?",
                                  (str(e), meta_id))
            except Exception:
                pass
    finally:
        scanner.STATE["running"] = False
        scanner.STATE["phase"] = "done"
        _FULL_SCAN_LOCK.release()


@app.post("/api/scan")
def start_scan():
    if scanner.STATE["running"]:
        return {"status": "already_running"}
    # Cross-process: also block if a different launcher is mid-scan
    with db.connect() as conn:
        running = conn.execute(
            "SELECT COUNT(*) AS n FROM scan_meta WHERE status='running' "
            "AND started_at > (strftime('%s', 'now') - 1800)"
        ).fetchone()
    if running and running["n"] > 0:
        return {"status": "already_running"}
    threading.Thread(target=_full_scan, daemon=True).start()
    return {"status": "started"}


@app.get("/api/disk")
def disk_stats():
    """Real disk usage from shutil.disk_usage + recovered total from quarantine."""
    import shutil
    usage = shutil.disk_usage("/")
    with db.connect() as conn:
        rec = conn.execute(
            "SELECT COALESCE(SUM(size), 0) AS s FROM quarantine WHERE status IN ('quarantined','purged')"
        ).fetchone()
    return {
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "recovered": rec["s"],
    }


@app.get("/api/status")
def app_status():
    """Single source of truth for the UI. Replaces the trio of /api/scan/status,
    /api/warmup/status, and /api/overview-poll. Returns one typed lifecycle
    state so the frontend has one switch statement, not three.
    """
    with db.connect() as conn:
        last_done = conn.execute(
            "SELECT id, finished_at, files_indexed, total_size, index_version "
            "FROM scan_meta WHERE status='done' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    has_data = last_done is not None
    is_warm = warmup.is_warm() if has_data else False

    if scanner.STATE["running"]:
        # Scan or warmup in progress
        if scanner.STATE.get("phase") == "warming":
            label = scanner.STATE.get("current") or "Optimizing…"
            progress = warmup.status().get("progress", 0)
            state_name = "warming" if not has_data else "ready_warming"
        else:
            label = scanner.STATE.get("current") or "Scanning…"
            count = scanner.STATE.get("count", 0)
            total = scanner.STATE.get("total", 0)
            progress = int((count / total) * 100) if total else 0
            state_name = "scanning" if not has_data else "ready_scanning"
    elif not has_data:
        state_name = "no_data"
        label = "Ready to scan"
        progress = 0
    elif not is_warm:
        state_name = "ready_warming_bg"
        label = "Warming cache…"
        progress = warmup.status().get("progress", 0)
        # Kick off warming if it's not already
        if not warmup.STATE["running"]:
            warmup.warm_async()
    else:
        state_name = "ready"
        label = "Up to date"
        progress = 100

    return {
        "state": state_name,
        "label": label,
        "progress": progress,
        "scan_id": last_done["id"] if last_done else None,
        "last_finished_at": last_done["finished_at"] if last_done else None,
        "files_indexed": last_done["files_indexed"] if last_done else 0,
        "total_size": last_done["total_size"] if last_done else 0,
        "index_version_current": scanner.INDEX_VERSION,
        "index_version_data": last_done["index_version"] if last_done else None,
        "is_stale": last_done is not None and (last_done["index_version"] or 0) < scanner.INDEX_VERSION,
        # Live counts during an in-flight scan
        "live_count": scanner.STATE.get("count", 0),
        "live_total": scanner.STATE.get("total", 0),
        "live_size": scanner.STATE.get("size", 0),
    }


@app.get("/api/scan/status")
def scan_status():
    return {
        "running": scanner.STATE["running"],
        "phase": scanner.STATE.get("phase", "idle"),
        "files_indexed": scanner.STATE["count"],
        "total": scanner.STATE.get("total", 0),
        "size": scanner.STATE["size"],
        "current": scanner.STATE["current"],
        "error": scanner.STATE["error"],
        "elapsed": time.time() - scanner.STATE["started_at"] if scanner.STATE["started_at"] else 0,
    }


@app.get("/api/warmup/status")
def warmup_status():
    """Combined boot status — frontend shows real progress until is_warm=true."""
    return {
        **warmup.status(),
        "is_warm": warmup.is_warm(),
    }


# ── Overview ──────────────────────────────────────────────────────────────────
def _build_overview() -> dict:
    """Live-compute the overview from the index. Slow on first call (~16s on 1.7M files)."""
    with db.connect() as conn:
        cats = conn.execute("""
            SELECT category, COUNT(*) AS files, SUM(size) AS size,
                   SUM(CASE WHEN regenerable=1 THEN size ELSE 0 END) AS recoverable
            FROM files GROUP BY category ORDER BY size DESC
        """).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS s FROM files").fetchone()
        recoverable = conn.execute(
            "SELECT COALESCE(SUM(size),0) AS s FROM files WHERE regenerable=1"
        ).fetchone()["s"]
        meta = conn.execute(
            "SELECT * FROM scan_meta WHERE status='done' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        q = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS s FROM quarantine WHERE status='quarantined'"
        ).fetchone()

    return {
        "total_files": total["n"],
        "total_size": total["s"],
        "recoverable": recoverable,
        "categories": [dict(c) for c in cats],
        "last_scan": dict(meta) if meta else None,
        "quarantine": {"count": q["n"], "size": q["s"]},
    }


@app.get("/api/overview")
def overview():
    cached = warmup.get("overview")
    if cached is not None:
        return cached
    return _build_overview()


# ── Sunburst ──────────────────────────────────────────────────────────────────
def _build_sunburst(depth: int = 4, min_pct: float = 0.001) -> dict:
    """Build a hierarchical tree from the file index.

    `min_pct` prunes nodes smaller than this fraction of the total root size.
    Default 0.001 (0.1%) drops ~95% of leaf nodes from the response on a
    typical 500 GB drive — taking us from 28k nodes to ~2k. Massive UI win
    with no real loss of information (those nodes are too small to render).
    """
    with db.connect() as conn:
        rows = conn.execute("SELECT path, size, category FROM files").fetchall()
    root = {"name": "/", "size": 0, "children": {}}
    for r in rows:
        parts = r["path"].strip("/").split("/")[:depth]
        node = root
        node["size"] += r["size"]
        for part in parts:
            child = node["children"].get(part)
            if child is None:
                child = {"name": part, "size": 0, "children": {}, "category": r["category"]}
                node["children"][part] = child
            child["size"] += r["size"]
            node = child
    threshold = int(root["size"] * min_pct)
    return _serialize(root, min_size=threshold)


@app.get("/api/sunburst")
def sunburst(depth: int = 4):
    if depth == 6:
        cached = warmup.get("sunburst")
        if cached is not None:
            return cached
    return _build_sunburst(depth)


def _serialize(node, min_size: int = 0):
    out = {"name": node["name"], "size": node["size"]}
    if "category" in node:
        out["category"] = node["category"]
    children = [
        _serialize(c, min_size=min_size)
        for c in node["children"].values()
        if c["size"] > min_size
    ]
    if children:
        out["children"] = sorted(children, key=lambda x: -x["size"])
    return out


# ── Files by category / smart action ──────────────────────────────────────────
@app.get("/api/category/{name}")
def category_files(name: str, limit: int = 500, sort: str = "size"):
    sort_col = {"size": "size DESC", "atime": "atime ASC", "mtime": "mtime DESC"}.get(sort, "size DESC")
    with db.connect() as conn:
        rows = conn.execute(
            f"""SELECT path, size, mtime, atime, subcategory, regenerable
                FROM files WHERE category=? ORDER BY {sort_col} LIMIT ?""",
            (name, limit),
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/action/{slug}/groups")
def action_groups(slug: str, limit: int = 80):
    """Return high-level groups (Brave / Chrome / npm…) instead of file lists."""
    if limit == 80:
        cached = warmup.get(f"action_groups:{slug}", scoped=True)
        if cached is not None:
            return cached
    return grouping.groups_for(slug, limit=limit)


@app.get("/api/group/files")
def group_files(root: str):
    """List files under a group root — used at quarantine time."""
    return grouping.files_under(root)


@app.get("/api/action/{slug}")
def action_files(slug: str, limit: int = 500):
    """Resolve a suggestion's action slug into the matching file list."""
    with db.connect() as conn:
        if slug.startswith("category:"):
            cat = slug.split(":", 1)[1]
            rows = conn.execute(
                "SELECT path, size, mtime, atime, subcategory FROM files WHERE category=? ORDER BY size DESC LIMIT ?",
                (cat, limit),
            ).fetchall()
        elif slug == "stale_artifacts":
            cutoff = time.time() - 180 * 86_400
            rows = conn.execute("""
                SELECT f.path, f.size, f.mtime, f.atime, f.subcategory
                FROM files f
                JOIN projects p ON f.path LIKE p.path || '/%'
                WHERE p.last_activity < ? AND f.category='dev_artifact'
                ORDER BY f.size DESC LIMIT ?
            """, (cutoff, limit)).fetchall()
        elif slug == "old_logs":
            cutoff = time.time() - 90 * 86_400
            rows = conn.execute(
                "SELECT path, size, mtime, atime, subcategory FROM files WHERE category='logs' AND mtime < ? ORDER BY size DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        elif slug == "old_downloads":
            cutoff = time.time() - 90 * 86_400
            rows = conn.execute(
                "SELECT path, size, mtime, atime, subcategory FROM files WHERE category='downloads' AND atime < ? ORDER BY size DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        elif slug == "forgotten":
            cutoff = time.time() - 365 * 86_400
            rows = conn.execute("""
                SELECT path, size, mtime, atime, subcategory FROM files
                WHERE size > 100*1024*1024 AND atime < ?
                  AND category NOT IN ('cache','dev_artifact','trash','logs')
                ORDER BY size DESC LIMIT ?
            """, (cutoff, limit)).fetchall()
        else:
            return []
        return [dict(r) for r in rows]


# ── Projects ──────────────────────────────────────────────────────────────────
@app.get("/api/projects")
def list_projects(limit: int = 50):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY artifact_size DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Forgotten files ───────────────────────────────────────────────────────────
@app.get("/api/forgotten")
def forgotten(min_size_mb: int = 100, min_age_days: int = 365, limit: int = 200):
    cutoff = time.time() - min_age_days * 86_400
    min_bytes = min_size_mb * 1024 * 1024
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT path, size, mtime, atime, category, subcategory
            FROM files
            WHERE size >= ? AND atime < ?
              AND category NOT IN ('cache','dev_artifact','trash','logs')
            ORDER BY size DESC LIMIT ?
        """, (min_bytes, cutoff, limit)).fetchall()
        return [dict(r) for r in rows]


# ── Suggestions ───────────────────────────────────────────────────────────────
@app.get("/api/suggestions")
def get_suggestions():
    cached = warmup.get("suggestions", scoped=True)
    if cached is not None:
        return cached
    return suggestions.generate()


# ── Quarantine ────────────────────────────────────────────────────────────────
@app.post("/api/quarantine")
def do_quarantine(req: QuarantineRequest):
    return {"results": quarantine.quarantine(req.paths)}


@app.get("/api/quarantine")
def list_q():
    return quarantine.list_quarantined()


@app.post("/api/quarantine/{qid}/restore")
def restore_q(qid: int):
    return quarantine.restore(qid)


@app.post("/api/quarantine/{qid}/purge")
def purge_q(qid: int):
    return quarantine.purge(qid)


# ── File detail / preview / actions ──────────────────────────────────────────
class PathRequest(BaseModel):
    path: str


@app.get("/api/file/detail")
def file_detail_route(path: str):
    return file_detail.detail(path)


@app.get("/api/file/preview")
def file_preview(path: str, size: int = 512):
    p = file_detail.preview(path, size=size)
    if not p:
        return Response(status_code=204)
    return FileResponse(p, media_type="image/png")


@app.post("/api/file/open")
def file_open(req: PathRequest):
    return {"ok": file_detail.open_default(req.path)}


@app.post("/api/file/reveal")
def file_reveal(req: PathRequest):
    return {"ok": file_detail.open_in_finder(req.path)}


@app.post("/api/file/quicklook")
def file_quicklook(req: PathRequest):
    """Open the native macOS Quick Look preview window — same as Spacebar in Finder."""
    return {"ok": file_detail.quicklook(req.path)}


# ── Sweep mode ───────────────────────────────────────────────────────────────
@app.get("/api/sweep/queue")
def sweep_queue(limit: int = 200):
    return sweep.queue(limit=limit)


# ── Smart Picks (AI-style predictive deletability ranking) ───────────────────
@app.get("/api/smart/picks")
def smart_picks(limit: int = 100, min_size_mb: int = 10):
    if limit == 120 and min_size_mb == 10:
        cached = warmup.get("smart_picks")
        if cached is not None:
            return cached
    return smart.picks(limit=limit, min_size_mb=min_size_mb)


# ── Labels (Phase 1 — human names + reasons) ─────────────────────────────────
@app.get("/api/label/resolve")
def label_resolve(path: str):
    return labels_mod.resolve_label(path)


# ── LLM auth (API key OR ChatGPT OAuth) ──────────────────────────────────────
class LLMKeyRequest(BaseModel):
    key: str
    model: str = "gpt-4o-mini"


@app.get("/api/llm/status")
def llm_status_route():
    return llm.llm_status()


@app.post("/api/llm/key")
def llm_set_key(req: LLMKeyRequest):
    return llm.set_key(req.key, req.model)


@app.delete("/api/llm/key")
def llm_clear_key():
    return llm.clear_key()


@app.get("/api/oauth/status")
def oauth_status():
    return oauth_chatgpt.status()


@app.post("/api/oauth/login")
def oauth_login_start():
    """Returns the URL the frontend should open in a browser tab. The callback
    handler runs in the background on port 1455."""
    return oauth_chatgpt.begin_login()


@app.get("/api/oauth/login/status")
def oauth_login_status():
    return oauth_chatgpt.login_status()


@app.post("/api/oauth/sign-out")
def oauth_sign_out():
    oauth_chatgpt.sign_out()
    return {"ok": True}


# ── On-demand "should I delete this?" verdict ────────────────────────────────
class FileVerdictRequest(BaseModel):
    path: str
    force: bool = False


@app.post("/api/file/verdict")
def file_verdict_route(req: FileVerdictRequest):
    return file_verdict.evaluate(req.path, force=req.force)


# Bulk variant: one prompt per file, but in parallel-ish via the same DB cache.
# Returns the same {ok, verdict, confidence, reason} envelope per path.
class FileVerdictBulkRequest(BaseModel):
    paths: List[str]
    force: bool = False


@app.post("/api/file/verdict/bulk")
def file_verdict_bulk(req: FileVerdictBulkRequest):
    if not req.paths:
        return {"results": []}
    results = []
    for p in req.paths[:50]:  # cap so a runaway click can't burn the LLM budget
        results.append({"path": p, **file_verdict.evaluate(p, force=req.force)})
    return {"results": results}


# Group-level verdict — "should I clear this whole folder?"
class GroupVerdictRequest(BaseModel):
    path: str
    force: bool = False


@app.post("/api/group/verdict")
def group_verdict_route(req: GroupVerdictRequest):
    return group_verdict.evaluate(req.path, force=req.force)


# ── Sweeper Audit (mass batched verdicts) ────────────────────────────────────
class AuditRunRequest(BaseModel):
    scope: str = "smart_picks"
    max_files: int = 100
    min_size_mb: int = 10


@app.post("/api/audit/run")
def audit_run(req: AuditRunRequest):
    return audit.start(req.scope, req.max_files, req.min_size_mb)


@app.get("/api/audit/status")
def audit_status_route():
    return audit.status()


@app.get("/api/audit/results")
def audit_results(min_confidence: int = 0, verdicts: str = "safe,review,keep", limit: int = 500):
    vs = [v.strip() for v in verdicts.split(",") if v.strip()]
    return audit.results(min_confidence=min_confidence, verdicts=vs, limit=limit)


@app.post("/api/audit/reset")
def audit_reset():
    audit.reset()
    return {"phase": "idle"}


# ── Self-update (voicetype-style: GitHub Releases tarball) ───────────────────
@app.get("/api/version")
def app_version():
    return {"version": updater.current_version()}


@app.get("/api/update/check")
def update_check():
    return updater.check_for_update()


class UpdateInstallRequest(BaseModel):
    force: bool = False


@app.post("/api/update/install")
def update_install(req: UpdateInstallRequest):
    return updater.install_update(force=req.force)


@app.get("/api/update/status")
def update_status():
    return updater.install_status()


@app.post("/api/update/relaunch")
def update_relaunch():
    return updater.relaunch()


# ── Integrator (third LLM auth path: PKCE-paired ChatGPT via broker) ─────────
@app.get("/api/integrator/status")
def integrator_status():
    """UI-facing snapshot: connected, email, expires_at, plus the ai_verdicts
    feature toggle. The button enable/disable logic on the frontend only
    needs this one call."""
    cfg = llm.load_config()
    return {
        **integrator_chat.status(),
        "ai_verdicts_enabled": bool(cfg.get("ai_verdicts_enabled")),
    }


@app.post("/api/integrator/connect")
def integrator_connect():
    """Run the OAuth dance. BLOCKING — opens the user's browser, waits up to
    5 min for the callback, returns the post-pair status. Frontend should
    show a 'browser opening' state during the wait."""
    try:
        s = integrator_chat.connect()
    except integrator_chat.IntegratorError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Pairing implies the feature is enabled.
    cfg = llm.load_config()
    cfg["ai_verdicts_enabled"] = True
    llm.save_config(cfg)
    return {**s, "ai_verdicts_enabled": True}


@app.post("/api/integrator/disconnect")
def integrator_disconnect():
    integrator_chat.disconnect()
    cfg = llm.load_config()
    cfg["ai_verdicts_enabled"] = False
    llm.save_config(cfg)
    return {"connected": False, "ai_verdicts_enabled": False}


class AiVerdictsToggleRequest(BaseModel):
    enabled: bool


@app.post("/api/integrator/ai-verdicts")
def integrator_set_ai_verdicts(req: AiVerdictsToggleRequest):
    """Toggle the AI-verdicts feature without unpairing. Lets the user keep
    tokens on file but hide the buttons."""
    cfg = llm.load_config()
    cfg["ai_verdicts_enabled"] = bool(req.enabled)
    llm.save_config(cfg)
    return {"ai_verdicts_enabled": cfg["ai_verdicts_enabled"]}


# ── Redundancies (Phase 5) ───────────────────────────────────────────────────
@app.get("/api/redundancies")
def get_redundancies():
    cached = warmup.get("redundancies")
    if cached is not None:
        return cached
    return redundancy.grouped_findings()


@app.post("/api/redundancies/sweep")
def sweep_redundancies(req: QuarantineRequest):
    """Quarantine the redundant_paths the frontend selected."""
    return {"results": quarantine.quarantine(req.paths)}


@app.post("/api/redundancies/detect")
def redetect_redundancies():
    """Force a re-detect (without rescanning files)."""
    counts = redundancy.detect_all()
    return {"counts": counts}


# ── Static frontend ───────────────────────────────────────────────────────────
@app.get("/")
def root(launch: int | None = None):
    """Serve index.html with mtime-based cache-bust on assets."""
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    css_v = int((FRONTEND / "styles.css").stat().st_mtime)
    js_v = int((FRONTEND / "app.js").stat().st_mtime)
    html = html.replace("/static/styles.css", f"/static/styles.css?v={css_v}")
    html = html.replace("/static/app.js", f"/static/app.js?v={js_v}")
    return Response(content=html, media_type="text/html; charset=utf-8")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

_boot_warmup_or_rescan()
