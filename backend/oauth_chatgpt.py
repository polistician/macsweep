"""Sign in with ChatGPT — PKCE OAuth against auth.openai.com.

Uses the Codex CLI's public client_id. Same flow OpenClaw uses.

Lifecycle:
  1. Frontend POSTs /api/oauth/login → returns auth_url
  2. Frontend opens the URL in a browser tab
  3. User signs in on auth.openai.com → 302 to http://localhost:1455/auth/callback
  4. Callback handler exchanges code → access_token + refresh_token, persists to config
  5. Frontend polls /api/oauth/login/status until ok|error|timeout

Tokens go in data/config.json under "openai_oauth". `llm_client` refreshes
on 401 transparently.
"""
import base64
import hashlib
import http.server
import json
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional

from . import llm


CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
AUTH_URL = ISSUER + "/oauth/authorize"
TOKEN_URL = ISSUER + "/oauth/token"
REDIRECT_PORT = 1455
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/auth/callback"
SCOPES = "openid profile email offline_access"


# Per-process state. Keyed by `state` so concurrent flows don't collide.
_PENDING: dict[str, dict] = {}    # state -> {"verifier": ..., "started": ts}
_RESULT: dict = {"status": "idle"}  # last result; consumed by the frontend
_SERVER_LOCK = threading.Lock()
_SERVER_STARTED = False


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


# ── Callback handler ──────────────────────────────────────────────────────────
class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a, **_k):  # silence stdlib console spam
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(url.query)
        code = (query.get("code") or [None])[0]
        state = (query.get("state") or [None])[0]
        err = (query.get("error") or [None])[0]
        if err:
            _RESULT.update(status="error", error=err)
            self._error_page(err)
            return
        if not code or not state:
            _RESULT.update(status="error", error="Missing code or state")
            self._error_page("Missing code or state")
            return
        pending = _PENDING.pop(state, None)
        if not pending:
            _RESULT.update(status="error", error="Unknown state — try again")
            self._error_page("Unknown state — try again")
            return
        try:
            tokens = _exchange_code(code, pending["verifier"])
            _save_tokens(tokens)
            _RESULT.update(status="ok", email=tokens.get("_email"))
            self._success_page(tokens.get("_email"))
        except Exception as e:
            _RESULT.update(status="error", error=str(e))
            self._error_page(str(e))

    def _success_page(self, email: Optional[str]):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = f"as {email}" if email else ""
        self.wfile.write(_HTML_OK.replace("{{EMAIL}}", msg).encode())

    def _error_page(self, error: str):
        self.send_response(400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_HTML_ERR.replace("{{ERROR}}", error).encode())


_HTML_OK = """<!doctype html><html><head><meta charset="utf-8"><title>MacSweep</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;background:#0a0a0c;color:#f5f5f7;
margin:0;height:100vh;display:flex;align-items:center;justify-content:center;text-align:center}
.card{padding:40px;max-width:420px}h1{font-size:22px;margin:0 0 8px}p{color:#a8a8ad;font-size:14px;line-height:1.55}</style></head>
<body><div class="card"><h1>Connected to ChatGPT</h1><p>You can close this tab and return to MacSweep {{EMAIL}}.</p></div></body></html>"""

_HTML_ERR = """<!doctype html><html><head><meta charset="utf-8"><title>MacSweep</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;background:#0a0a0c;color:#f5f5f7;
margin:0;height:100vh;display:flex;align-items:center;justify-content:center;text-align:center}
.card{padding:40px;max-width:420px}h1{font-size:22px;margin:0 0 8px;color:#fca5a5}p{color:#a8a8ad;font-size:14px;line-height:1.55}</style></head>
<body><div class="card"><h1>Sign-in failed</h1><p>{{ERROR}}</p><p>Close this tab and try again from MacSweep settings.</p></div></body></html>"""


def _ensure_callback_server() -> None:
    """Start the background HTTP server on 1455 if not already running."""
    global _SERVER_STARTED
    with _SERVER_LOCK:
        if _SERVER_STARTED:
            return
        try:
            server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
        except OSError as e:
            print(f"[OAuth] callback server failed to start: {e}")
            raise
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _SERVER_STARTED = True


# ── Token exchange / refresh ─────────────────────────────────────────────────
def _exchange_code(code: str, verifier: str) -> dict:
    """Exchange the authorization code for tokens. Raises on failure."""
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Token exchange failed (HTTP {e.code}): {e.read().decode()[:200]}")
    data["_email"] = _email_from_jwt(data.get("id_token"))
    return data


def _save_tokens(tokens: dict) -> None:
    cfg = llm.load_config()
    cfg["openai_oauth"] = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": int(time.time()) + int(tokens.get("expires_in", 600)),
        "id_token": tokens.get("id_token"),
    }
    if tokens.get("_email"):
        cfg["oauth_email"] = tokens["_email"]
    llm.save_config(cfg)


def _email_from_jwt(id_token: Optional[str]) -> Optional[str]:
    if not id_token:
        return None
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email")
    except Exception:
        return None


def refresh() -> Optional[str]:
    """Refresh the access token using the stored refresh_token. Returns the new
    access token on success, None on failure."""
    cfg = llm.load_config()
    oauth = cfg.get("openai_oauth") or {}
    rt = oauth.get("refresh_token")
    if not rt:
        return None
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": rt,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"[OAuth] refresh failed: HTTP {e.code} {e.read().decode()[:200]}")
        return None
    data["_email"] = oauth.get("oauth_email")
    _save_tokens(data)
    return data["access_token"]


# ── Public API ───────────────────────────────────────────────────────────────
def _open_in_browser(url: str) -> None:
    """Hand the URL to the OS default browser. pywebview's WKWebView swallows
    window.open() under private_mode, so we open from the backend instead."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", url])
        elif sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    except Exception as e:
        print(f"[OAuth] couldn't launch browser: {e}")


def begin_login() -> dict:
    """Build the auth URL, launch it in the user's default browser, return the
    URL too in case the frontend wants to display a fallback link."""
    try:
        _ensure_callback_server()
    except OSError as e:
        return {"ok": False, "error": f"Could not bind localhost:{REDIRECT_PORT} ({e})"}
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    _PENDING[state] = {"verifier": verifier, "started": time.time()}
    _RESULT.update(status="pending")
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{AUTH_URL}?{params}"
    _open_in_browser(auth_url)
    return {"ok": True, "auth_url": auth_url, "state": state}


def login_status() -> dict:
    """Polled by the frontend. Snapshots current result then resets."""
    return dict(_RESULT)


def status() -> dict:
    cfg = llm.load_config()
    oauth = cfg.get("openai_oauth") or {}
    if not oauth.get("access_token"):
        return {"signed_in": False}
    expires_at = int(oauth.get("expires_at", 0))
    return {
        "signed_in": True,
        "email": cfg.get("oauth_email"),
        "expires_in_days": max(0, (expires_at - time.time()) / 86400),
    }


def sign_out() -> None:
    cfg = llm.load_config()
    cfg.pop("openai_oauth", None)
    cfg.pop("oauth_email", None)
    llm.save_config(cfg)
