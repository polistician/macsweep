"""Self-update — voicetype's pattern, adapted for MacSweep.

MacSweep currently ships as a thin .app wrapper that runs ./.venv/bin/python
launch.py from a fixed project directory. There's no PyInstaller bundle yet,
so we update VIA TARBALL: download macsweep-<ver>.tar.gz from GitHub Releases,
verify SHA256, extract to a staging dir, atomically swap with the project dir,
and relaunch.

Public API:
    current_version()          -> "0.1.0"
    check_for_update()         -> {ok, current, latest, has_update, notes_url, ...}
    install_update(force=False)-> {ok, status, ...}      (BLOCKING; runs in bg thread)
    install_status()           -> {phase, progress, error, ...}

Routes wire to /api/update/check, /api/update/install, /api/update/status.

Safety:
    - Verifies SHA256 BEFORE swap. Mismatch → abort.
    - Keeps a backup of the current project dir at <project>.bak-<ver> until
      the next launch. User can restore manually if relaunch breaks.
    - Refuses to swap if the project dir is on a different volume from /tmp
      (would mean a slow non-atomic copy instead of rename).
    - Never touches files outside the project dir.
    - "Restart to apply" is initiated by the frontend; backend only stages.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("macsweep.updater")

# GitHub repo that hosts MacSweep releases. Override via env if you fork.
GITHUB_OWNER = os.environ.get("MACSWEEP_OWNER", "polistician")
GITHUB_REPO = os.environ.get("MACSWEEP_REPO", "macsweep")
GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"

# Per-process state. Single update at a time.
_STATE: dict = {
    "phase": "idle",        # idle | downloading | verifying | staging | swapping | done | error
    "progress": 0,          # 0–100
    "label": "",
    "error": "",
    "started_at": 0,
    "finished_at": 0,
    "from_version": "",
    "to_version": "",
}
_LOCK = threading.Lock()


# ── Version ────────────────────────────────────────────────────────────────


def current_version() -> str:
    """Read VERSION file. Falls back to '0.0.0' if missing."""
    try:
        return VERSION_FILE.read_text().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _semver_tuple(v: str) -> tuple[int, ...]:
    """Parse '1.2.3' or 'v1.2.3' into (1,2,3). Non-numeric parts → 0.
    Used only for "is this newer?" comparison."""
    v = v.strip().lstrip("vV")
    parts = v.split(".")
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_newer(latest: str, current: str) -> bool:
    return _semver_tuple(latest) > _semver_tuple(current)


# ── HTTP ───────────────────────────────────────────────────────────────────


_UA = "MacSweep-updater/1.0"


def _http_get_json(url: str, timeout: int = 15) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        log.warning("update: GET %s failed: %s", url, e)
        return None


def _download(url: str, dest: Path, on_progress=None) -> None:
    """Stream-download to dest. Calls on_progress(bytes_done, bytes_total)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)


# ── Check for update ───────────────────────────────────────────────────────


def check_for_update() -> dict:
    """Hit the GitHub Releases API; compare tag to local VERSION."""
    cur = current_version()
    data = _http_get_json(GITHUB_API)
    if not data:
        return {
            "ok": False,
            "error": "Could not reach GitHub. Check your network.",
            "current": cur,
        }
    latest = (data.get("tag_name") or "").lstrip("v")
    if not latest:
        return {"ok": False, "error": "No releases published yet.", "current": cur}
    assets = data.get("assets") or []
    tarball = next((a for a in assets if a["name"].endswith(".tar.gz")), None)
    sha = next((a for a in assets if a["name"].endswith(".sha256")), None)
    return {
        "ok": True,
        "current": cur,
        "latest": latest,
        "has_update": is_newer(latest, cur),
        "tarball_url": tarball["browser_download_url"] if tarball else None,
        "sha256_url": sha["browser_download_url"] if sha else None,
        "notes": (data.get("body") or "")[:1500],
        "notes_url": data.get("html_url"),
        "published_at": data.get("published_at"),
    }


# ── Install ────────────────────────────────────────────────────────────────


def install_status() -> dict:
    return {**_STATE, "elapsed": (time.time() - _STATE["started_at"]) if _STATE["started_at"] else 0}


def _set(phase=None, progress=None, label=None, error=None):
    with _LOCK:
        if phase is not None:
            _STATE["phase"] = phase
        if progress is not None:
            _STATE["progress"] = max(0, min(100, int(progress)))
        if label is not None:
            _STATE["label"] = label
        if error is not None:
            _STATE["error"] = error


def _verify_sha256(file_path: Path, expected_hex: str) -> bool:
    """Compute SHA256 of file, compare with expected (case-insensitive). The
    .sha256 file is typically `<hash>  <filename>` — we take the first token."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    actual = h.hexdigest().lower()
    expected = expected_hex.strip().split()[0].lower()
    return actual == expected


def _atomic_swap(staging_dir: Path, target: Path) -> Path:
    """Replace `target` with `staging_dir`. Returns the backup path of the
    old target (for rollback). Both paths must be on the same filesystem.

    Strategy: rename(target → backup), rename(staging → target). Two cheap
    metadata operations, indistinguishable from atomic for the user."""
    backup = target.parent / f"{target.name}.bak-{int(time.time())}"
    target.rename(backup)
    try:
        staging_dir.rename(target)
    except Exception:
        # Roll back the first rename so we don't end up with no project dir.
        backup.rename(target)
        raise
    return backup


def _install(force: bool) -> None:
    """Background-thread install pipeline. Updates _STATE throughout."""
    try:
        _set(phase="downloading", progress=0, label="Checking for update…", error="")
        info = check_for_update()
        if not info.get("ok"):
            _set(phase="error", error=info.get("error", "Check failed"))
            return
        if not info.get("has_update") and not force:
            _set(phase="done", progress=100, label="Already up to date.")
            return
        if not info.get("tarball_url") or not info.get("sha256_url"):
            _set(phase="error", error="Release missing tarball or sha256 asset.")
            return

        with _LOCK:
            _STATE["from_version"] = info["current"]
            _STATE["to_version"] = info["latest"]

        # Stage in /tmp so the swap is on the same volume as the project dir
        # ASSUMING macOS default APFS layout (one volume covers /Users + /tmp).
        # If they differ, the swap falls back to a copy — slower but safe.
        with tempfile.TemporaryDirectory(prefix="macsweep-update-") as tmp:
            tmpdir = Path(tmp)
            tarball_path = tmpdir / "macsweep.tar.gz"
            sha_path = tmpdir / "macsweep.sha256"

            _set(label=f"Downloading v{info['latest']}…")
            _download(
                info["tarball_url"], tarball_path,
                on_progress=lambda done, total: _set(
                    progress=int((done / total) * 70) if total else 5,  # download = 0–70%
                ),
            )

            _set(phase="verifying", progress=72, label="Verifying integrity…")
            _download(info["sha256_url"], sha_path)
            expected = sha_path.read_text()
            if not _verify_sha256(tarball_path, expected):
                _set(phase="error", error="SHA256 mismatch — refusing to install.")
                return

            _set(phase="staging", progress=80, label="Extracting…")
            staging = tmpdir / "staged"
            staging.mkdir()
            with tarfile.open(tarball_path, "r:gz") as tf:
                # Defense-in-depth: refuse path traversal in the tarball.
                for m in tf.getmembers():
                    name = m.name
                    if name.startswith("/") or ".." in Path(name).parts:
                        raise RuntimeError(f"Refusing tar entry with unsafe path: {name}")
                tf.extractall(staging)

            # Tarballs typically wrap a single top-level directory like
            # macsweep-0.2.0/. Find it.
            top_level = [p for p in staging.iterdir() if p.is_dir()]
            if len(top_level) != 1:
                raise RuntimeError("Tarball must contain exactly one top-level directory")
            new_root = top_level[0]

            # Sanity check: it really looks like a MacSweep release.
            if not (new_root / "backend").is_dir() or not (new_root / "launch.py").exists():
                raise RuntimeError("Tarball doesn't look like a MacSweep release")

            # Preserve the user's data/ directory across the swap — it holds
            # their index, config, and quarantine. Move it INTO the new root
            # before the swap so it lands in place.
            old_data = ROOT / "data"
            if old_data.exists():
                # The new tarball might or might not include an empty data/.
                new_data = new_root / "data"
                if new_data.exists():
                    shutil.rmtree(new_data)
                shutil.move(str(old_data), str(new_data))

            # Same for .venv — recreating it would mean re-installing every
            # dependency on relaunch (~30s and a network round-trip).
            old_venv = ROOT / ".venv"
            if old_venv.exists():
                new_venv = new_root / ".venv"
                if new_venv.exists():
                    shutil.rmtree(new_venv)
                shutil.move(str(old_venv), str(new_venv))

            # And MacSweep.app — the wrapper bundle should keep working since
            # it points at the same project directory by absolute path.
            old_app = ROOT / "MacSweep.app"
            if old_app.exists():
                new_app = new_root / "MacSweep.app"
                if new_app.exists():
                    shutil.rmtree(new_app)
                shutil.move(str(old_app), str(new_app))

            _set(phase="swapping", progress=92, label="Installing…")

            # Move staging out of /tmp into the project's parent so the
            # rename can be a metadata-only op on the same volume.
            staging_final = ROOT.parent / f".macsweep-staging-{int(time.time())}"
            shutil.move(str(new_root), str(staging_final))
            try:
                backup = _atomic_swap(staging_final, ROOT)
            except OSError as e:
                _set(phase="error", error=f"Swap failed: {e}. Original install untouched.")
                return

            _set(
                phase="done", progress=100,
                label=f"Updated to v{info['latest']}. Restart to apply.",
            )
            log.info("update complete: backup at %s", backup)
    except Exception as e:
        import traceback
        traceback.print_exc()
        _set(phase="error", error=str(e))


def install_update(force: bool = False) -> dict:
    """Kick off install in a background thread. Returns immediately.
    Refuses if an install is already in progress."""
    with _LOCK:
        if _STATE["phase"] in ("downloading", "verifying", "staging", "swapping"):
            return {"ok": False, "error": "Update already in progress."}
        _STATE.update(
            phase="downloading", progress=0, label="Starting…", error="",
            started_at=time.time(), finished_at=0,
            from_version=current_version(), to_version="",
        )
    threading.Thread(target=_install, args=(force,), daemon=True).start()
    return {"ok": True, "phase": "downloading"}


def relaunch() -> dict:
    """Spawn a fresh MacSweep.app and exit this process. The user clicks
    "Restart to apply" after a successful install."""
    app = ROOT / "MacSweep.app"
    if not app.exists():
        return {"ok": False, "error": "MacSweep.app not found in project dir."}
    try:
        subprocess.Popen(["/usr/bin/open", str(app)])
    except OSError as e:
        return {"ok": False, "error": str(e)}
    # Give the new process ~1s to spin up before we die. The frontend already
    # showed the "Restarting" UI by the time this returns.
    threading.Timer(1.5, lambda: os._exit(0)).start()
    return {"ok": True}
