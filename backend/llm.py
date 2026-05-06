"""LLM auth + config storage.

Two modes, picked at call time by `llm_client`:
  - api_key — user pasted an OpenAI sk-... key (data/config.json)
  - oauth   — user signed in with ChatGPT (Codex OAuth, tokens in same file)

Nothing leaves the machine except what `file_verdict` explicitly sends.
"""
import json
import time
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"

DEFAULT_MODEL = "gpt-4o-mini"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def llm_status() -> dict:
    cfg = load_config()
    key = cfg.get("openai_api_key")
    oauth = cfg.get("openai_oauth") or {}
    has_oauth = bool(oauth.get("access_token"))
    has_key = bool(key)
    if has_key:
        mode = "api_key"
    elif has_oauth:
        mode = "oauth"
    else:
        mode = "none"
    return {
        "auth_mode": mode,
        "configured": has_key or has_oauth,
        "model": cfg.get("openai_model", DEFAULT_MODEL),
        "key_preview": (key[:7] + "…" + key[-4:]) if has_key else None,
    }


def set_key(key: str, model: str = DEFAULT_MODEL) -> dict:
    cfg = load_config()
    cfg["openai_api_key"] = key
    cfg["openai_model"] = model or DEFAULT_MODEL
    save_config(cfg)
    return {"ok": True}


def clear_key() -> dict:
    cfg = load_config()
    cfg.pop("openai_api_key", None)
    save_config(cfg)
    return {"ok": True}
