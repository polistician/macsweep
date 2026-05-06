"""Unified LLM chat client.

Two auth modes (configured in data/config.json):
  - api_key  → OpenAI public API at https://api.openai.com/v1/chat/completions
               using the user's sk-... key. Bills against API credit.
  - oauth    → ChatGPT-Plus piggyback via Codex's private backend at
               https://chatgpt.com/backend-api/codex/responses, with the OAuth
               access token. Bills against the ChatGPT subscription. This is
               the same path the Codex CLI / OpenClaw use; the public API
               doesn't accept ChatGPT OAuth tokens (insufficient_quota).

API key takes precedence when both are configured. 401 on the OAuth path
triggers a refresh-and-retry once.
"""
import base64
import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional

from . import llm, oauth_chatgpt


# Public OpenAI API (key-based)
CHAT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_KEY_MODEL = "gpt-4o-mini"

# Codex backend (OAuth / ChatGPT subscription)
CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_CODEX_MODEL = "gpt-5.3-codex"


def _bearer_token() -> tuple[Optional[str], Optional[str]]:
    """Return (token, mode). Mode is 'api_key', 'oauth', or None."""
    cfg = llm.load_config()
    key = cfg.get("openai_api_key")
    if key:
        return key, "api_key"
    oauth = cfg.get("openai_oauth") or {}
    tok = oauth.get("access_token")
    if not tok:
        return None, None
    if int(oauth.get("expires_at", 0)) - 60 < time.time():
        new = oauth_chatgpt.refresh()
        return (new, "oauth") if new else (None, None)
    return tok, "oauth"


def _chatgpt_account_id(token: str) -> Optional[str]:
    """Extract chatgpt_account_id from the access-token JWT. The Codex backend
    requires this header — without it the request is rejected."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
    except Exception:
        return None


# ── Public API path (api_key mode) ───────────────────────────────────────────
def _chat_via_api_key(token: str, system: str, user: str, model: str,
                     max_tokens: int, temperature: float) -> Optional[str]:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()
    req = urllib.request.Request(CHAT_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        return data["choices"][0]["message"]["content"]
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError):
        return None


# ── Codex backend path (oauth mode) ──────────────────────────────────────────
def _chat_via_codex(token: str, system: str, user: str, model: str) -> Optional[str]:
    """Streamed Responses-API request, joins all output_text deltas. Codex
    rejects non-streaming, so we always stream and assemble locally."""
    acct = _chatgpt_account_id(token)
    if not acct:
        return None
    body = json.dumps({
        "model": model,
        "instructions": system,
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": user}]}],
        "store": False,
        "stream": True,
    }).encode()
    req = urllib.request.Request(CODEX_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
        "chatgpt-account-id": acct,
        "session_id": str(uuid.uuid4()),
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = ""
            for raw in r:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "response.output_text.delta":
                    text += ev.get("delta", "")
                elif ev.get("type") == "response.completed":
                    break
            return text or None
    except urllib.error.HTTPError as e:
        # 401 → token expired between the bearer check and the call. Caller
        # will retry once with a freshly-refreshed token.
        if e.code == 401:
            raise
        return None
    except (urllib.error.URLError, ConnectionError):
        return None


# ── Public ───────────────────────────────────────────────────────────────────
def chat_text(*, system: str, user: str, model: Optional[str] = None,
              max_tokens: int = 200, temperature: float = 0.4) -> Optional[str]:
    token, mode = _bearer_token()
    if not token:
        return None
    cfg = llm.load_config()

    if mode == "api_key":
        return _chat_via_api_key(token,
                                 system=system, user=user,
                                 model=model or cfg.get("openai_model") or DEFAULT_KEY_MODEL,
                                 max_tokens=max_tokens, temperature=temperature)

    # oauth — Codex backend
    chosen = model or cfg.get("openai_model") or DEFAULT_CODEX_MODEL
    try:
        return _chat_via_codex(token, system=system, user=user, model=chosen)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            new = oauth_chatgpt.refresh()
            if not new:
                return None
            try:
                return _chat_via_codex(new, system=system, user=user, model=chosen)
            except Exception:
                return None
        return None


def chat_json(*, system: str, user: str, **kw) -> Optional[dict]:
    """Variant that parses the response as JSON. Tolerates ```json fences."""
    txt = chat_text(system=system, user=user, **kw)
    if not txt:
        return None
    txt = txt.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.startswith("json"):
            txt = txt[4:]
    # Codex sometimes wraps JSON inside prose; grab the first {...} block.
    if not txt.startswith("{"):
        start = txt.find("{")
        end = txt.rfind("}")
        if start >= 0 and end > start:
            txt = txt[start:end + 1]
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return None
