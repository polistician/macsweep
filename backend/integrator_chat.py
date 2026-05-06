"""Integrator-as-IdP — third LLM auth mode for MacSweep.

Sits alongside the existing `api_key` and `oauth` modes (in `llm.py`).
When the user picks Integrator, MacSweep:

  1. Connects via OAuth 2.1 + PKCE to https://integrator.polistician.ai
     (browser opens, user signs in, Integrator redirects to localhost,
      MacSweep captures the code and exchanges it for an iat_ token).
  2. Stores {access_token, refresh_token, expires_at} in data/config.json
     under the `integrator` key.
  3. Calls POST integrator.polistician.ai/api/v1/chat/completions with
     `Authorization: Bearer iat_…` for every chat request. Integrator
     looks up the user's vaulted ChatGPT subscription tokens (linked
     once via the Integrator console), forwards to Codex, returns the
     OpenAI-compatible response.

Why use this instead of the existing oauth path?
  - One pairing covers every polistician.ai app (HablaDaily, MacroDaily,
    future ones). Pair ChatGPT once on Integrator, every app benefits.
  - Refresh, audit, rate limits handled centrally.
  - No more managing OpenAI's oauth_chatgpt private endpoints in MacSweep.

The `chat_json()` API mirrors `llm_client.chat_json()` so callers
(`file_verdict.py`, etc.) can swap without further changes.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional

from . import llm

log = logging.getLogger("macsweep.integrator")


# ── Configuration ──────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://integrator.polistician.ai"

# Public client (PKCE-only, no secret). Registered via /api/admin/apps.
DEFAULT_CLIENT_ID = "macsweep-3b5901"

# Both 127.0.0.1 and localhost must be on the app's redirect_uri allowlist.
LOCAL_PORT = 1717
LOCAL_REDIRECT_URI = f"http://127.0.0.1:{LOCAL_PORT}/auth/callback"

DEFAULT_SCOPES = ("chatgpt:chat",)

# How early before expiry we treat the access token as stale and refresh.
REFRESH_SKEW_S = 60


# ── Config helpers (live next to the existing api_key / oauth state) ───────


def _cfg_load() -> dict:
    return llm.load_config()


def _cfg_save(cfg: dict) -> None:
    llm.save_config(cfg)


def _read() -> dict:
    cfg = _cfg_load()
    return dict(cfg.get("integrator") or {})


def _write(state: dict) -> None:
    cfg = _cfg_load()
    cfg["integrator"] = state
    _cfg_save(cfg)


def _clear() -> None:
    cfg = _cfg_load()
    cfg.pop("integrator", None)
    _cfg_save(cfg)


# ── PKCE primitives ────────────────────────────────────────────────────────


def _make_pkce() -> tuple[str, str]:
    """Returns (verifier, challenge_S256). Verifier kept locally, challenge
    sent to /oauth/authorize."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ── HTTP helper (stdlib, no extra deps) ────────────────────────────────────

# Cloudflare in front of integrator.polistician.ai blocks Python's default
# urllib User-Agent (returns 403 / error 1010). Send a real browser-ish UA
# on every request so we look like any other client.
_DEFAULT_UA = "Mozilla/5.0 (MacSweep/1.0; +https://github.com/polistician/macsweep)"


def _post_form(url: str, data: dict, *, timeout: int = 20) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": _DEFAULT_UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8")
        except Exception:
            err = str(e)
        raise IntegratorError(f"HTTP {e.code}: {err}") from None


def _post_json(url: str, data: dict, headers: dict, *, timeout: int = 30) -> dict:
    body = json.dumps(data).encode("utf-8")
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _DEFAULT_UA,
        **headers,
    }
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8")
        except Exception:
            err = str(e)
        raise IntegratorError(f"HTTP {e.code}: {err}") from None


class IntegratorError(RuntimeError):
    """Anything that prevents a chat — auth missing, refresh failed, upstream 4xx."""


# ── Local callback server ──────────────────────────────────────────────────


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """One-shot handler that grabs ?code=&state= from the redirect.

    Stashes results on the server instance so the main thread can read them.
    """

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.server.captured = params  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        # Friendly success page; the user can close the tab.
        self.wfile.write(b"""
<!doctype html><html><head><meta charset=utf-8><title>MacSweep paired</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; margin: 0; background: #0b0b0e; color: #eceded; }
  .card { max-width: 360px; padding: 32px; border: 1px solid rgba(255,255,255,.1);
          border-radius: 14px; text-align: center; }
  h1 { font-size: 22px; margin: 0 0 12px; font-weight: 600; }
  p { color: #8a8a92; margin: 0; line-height: 1.5; }
</style></head>
<body><div class=card>
  <h1>Paired with Integrator.</h1>
  <p>You can close this tab and return to MacSweep.</p>
</div></body></html>
""")

    def log_message(self, *args, **kwargs):  # silence default access logs
        pass


def _start_callback_server(port: int = LOCAL_PORT) -> http.server.HTTPServer:
    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.captured = None  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ── Public API ─────────────────────────────────────────────────────────────


def status() -> dict:
    """Return UI-facing snapshot. Safe to call always."""
    state = _read()
    if not state.get("access_token"):
        return {"connected": False}
    return {
        "connected": True,
        "base_url": state.get("base_url") or DEFAULT_BASE_URL,
        "client_id": state.get("client_id") or DEFAULT_CLIENT_ID,
        "scope": state.get("scope") or " ".join(DEFAULT_SCOPES),
        "expires_at": state.get("expires_at"),
        "user_email": state.get("user_email"),
    }


def is_connected() -> bool:
    return bool(_read().get("access_token"))


def disconnect() -> None:
    _clear()


def connect(
    *,
    base_url: str = DEFAULT_BASE_URL,
    client_id: str = DEFAULT_CLIENT_ID,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
    open_browser: bool = True,
    timeout_s: int = 300,
) -> dict:
    """Run the OAuth dance against Integrator. Blocks until done or timeout.

    Returns the final state dict. On error raises IntegratorError.

    If `open_browser=False`, the function prints the auth URL and waits — the
    caller can render that URL themselves (used by tests + CLI in 'manual' mode).
    """
    # Refuse if the port is busy — almost always means another MacSweep run
    # didn't clean up. Surface clearly.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCAL_PORT))
    except OSError:
        raise IntegratorError(
            f"port {LOCAL_PORT} is already in use — close the other MacSweep "
            "instance or wait 30s for the old one to release the port"
        )
    finally:
        sock.close()

    verifier, challenge = _make_pkce()
    state = secrets.token_urlsafe(32)

    auth_url = (
        f"{base_url.rstrip('/')}/oauth/authorize?"
        + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": LOCAL_REDIRECT_URI,
                "scope": " ".join(scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )

    server = _start_callback_server(LOCAL_PORT)
    try:
        if open_browser:
            opened = webbrowser.open(auth_url)
            if not opened:
                log.warning("could not open browser; paste this URL: %s", auth_url)
        else:
            print(auth_url)

        # Poll until the callback fires or we time out.
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            captured = getattr(server, "captured", None)
            if captured:
                break
            time.sleep(0.2)
        else:
            raise IntegratorError("timed out waiting for OAuth callback")

        captured = server.captured  # type: ignore[attr-defined]
        if "error" in captured:
            raise IntegratorError(
                f"oauth error: {captured.get('error')}: "
                f"{captured.get('error_description', '')}"
            )

        if captured.get("state") != state:
            raise IntegratorError("state mismatch — possible CSRF; re-run connect")

        code = captured.get("code")
        if not code:
            raise IntegratorError("callback missing code parameter")

        # Exchange code → tokens.
        token_resp = _post_form(
            f"{base_url.rstrip('/')}/oauth/token",
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": LOCAL_REDIRECT_URI,
                "code_verifier": verifier,
                "client_id": client_id,
            },
        )

        access_token = token_resp.get("access_token")
        refresh_token = token_resp.get("refresh_token")
        expires_in = int(token_resp.get("expires_in", 3600))
        if not access_token:
            raise IntegratorError(f"token endpoint missing access_token: {token_resp}")

        new_state = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(time.time()) + expires_in,
            "scope": token_resp.get("scope") or " ".join(scopes),
            "base_url": base_url,
            "client_id": client_id,
            "paired_at": int(time.time()),
        }

        # Best-effort: enrich with the user's email from /api/auth/me. Skip on
        # failure — Integrator may not expose this anonymously.
        try:
            me = _http_get_json(
                f"{base_url.rstrip('/')}/api/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if isinstance(me, dict) and me.get("email"):
                new_state["user_email"] = me["email"]
        except Exception:
            pass

        _write(new_state)
        return status()
    finally:
        server.shutdown()


def _http_get_json(url: str, headers: dict, *, timeout: int = 10) -> dict:
    h = {"User-Agent": _DEFAULT_UA, "Accept": "application/json", **headers}
    req = urllib.request.Request(url, headers=h, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _refresh_if_needed() -> str:
    """Return a fresh access_token; refresh in place if near expiry. Raises
    IntegratorError if no refresh_token or refresh fails."""
    state = _read()
    if not state.get("access_token"):
        raise IntegratorError("not paired — run `integrator_chat connect` first")

    expires_at = int(state.get("expires_at") or 0)
    if expires_at and (expires_at - int(time.time())) > REFRESH_SKEW_S:
        return state["access_token"]

    refresh_token = state.get("refresh_token")
    if not refresh_token:
        raise IntegratorError("access token expired and no refresh_token on file")

    base_url = state.get("base_url") or DEFAULT_BASE_URL
    client_id = state.get("client_id") or DEFAULT_CLIENT_ID

    body = _post_form(
        f"{base_url.rstrip('/')}/oauth/token",
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
    )
    new_access = body.get("access_token")
    if not new_access:
        raise IntegratorError(f"refresh missing access_token: {body}")

    state["access_token"] = new_access
    state["refresh_token"] = body.get("refresh_token") or refresh_token
    state["expires_at"] = int(time.time()) + int(body.get("expires_in", 3600))
    _write(state)
    return new_access


def chat(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 512,
    response_format: Optional[dict] = None,
    timeout_s: int = 30,
) -> dict:
    """Call Integrator's chat-completions endpoint. Returns the parsed JSON
    response (OpenAI-compatible: `{choices: [{message: {content: ...}}], ...}`).

    Auto-refreshes the access token if expired. Raises IntegratorError on any
    upstream failure.
    """
    state = _read()
    if not state.get("access_token"):
        raise IntegratorError("not paired — run `integrator_chat connect` first")

    base_url = state.get("base_url") or DEFAULT_BASE_URL
    token = _refresh_if_needed()

    body: dict = {
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
    }
    if model:
        body["model"] = model
    if temperature is not None:
        body["temperature"] = temperature
    if response_format:
        body["response_format"] = response_format

    try:
        return _post_json(
            f"{base_url.rstrip('/')}/api/v1/chat/completions",
            body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,
        )
    except IntegratorError as e:
        # If the broker says 401 once, our cached access_token is stale beyond
        # what expires_at indicated — force a refresh and retry once.
        if "HTTP 401" in str(e):
            log.info("integrator chat 401 — forcing refresh and retrying once")
            state["expires_at"] = 0
            _write(state)
            token = _refresh_if_needed()
            return _post_json(
                f"{base_url.rstrip('/')}/api/v1/chat/completions",
                body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout_s,
            )
        raise


def chat_json(
    *,
    system: str,
    user: str,
    max_tokens: int = 512,
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> Optional[dict]:
    """JSON-mode wrapper used by file_verdict.py.

    Mirrors the existing `llm_client.chat_json()` signature so consumers
    can swap with no other changes. Returns the parsed JSON object the
    model produced, or None on parse failure.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    resp = chat(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    try:
        content = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        log.warning("chat_json: model did not return valid JSON: %.200s", content)
        return None


# ── CLI: `python -m backend.integrator_chat <cmd>` ─────────────────────────


def _cli(argv: list[str]) -> int:
    cmd = (argv[0] if argv else "status").lower()
    if cmd == "connect":
        try:
            print(json.dumps(connect(), indent=2))
            print("\n✓ paired. Try: python -m backend.integrator_chat test")
            return 0
        except IntegratorError as e:
            print(f"✗ {e}")
            return 1
    if cmd == "disconnect":
        disconnect()
        print("✓ disconnected")
        return 0
    if cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if cmd == "test":
        prompt = " ".join(argv[1:]) or "Reply with the single word: works"
        try:
            r = chat([{"role": "user", "content": prompt}], max_tokens=20)
            print(r["choices"][0]["message"]["content"])
            return 0
        except IntegratorError as e:
            print(f"✗ {e}")
            return 1
    print("usage: python -m backend.integrator_chat {connect|disconnect|status|test [prompt]}")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
