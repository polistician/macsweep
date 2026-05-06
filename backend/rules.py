"""File classification rules.

Rules are evaluated in declaration order — first match wins. Each rule maps a
path to (category, subcategory, regenerable). Regenerable means the file can be
recreated by re-downloading or rerunning a build, so it's always a Tier 1 win.
"""
from pathlib import Path

HOME = str(Path.home())

RULES = []


def rule(category, subcategory=None, regenerable=False):
    def decorator(predicate):
        RULES.append((predicate, category, subcategory, regenerable))
        return predicate
    return decorator


# ── Trash ─────────────────────────────────────────────────────────────────────
@rule("trash", "user_trash", regenerable=True)
def _trash(p):
    return p.startswith(f"{HOME}/.Trash/")


# ── Dev artifacts (regenerable) ───────────────────────────────────────────────
@rule("dev_artifact", "node_modules", regenerable=True)
def _node_modules(p):
    return "/node_modules/" in p


@rule("dev_artifact", "xcode_derived", regenerable=True)
def _xcode_derived(p):
    return f"{HOME}/Library/Developer/Xcode/DerivedData/" in p


@rule("dev_artifact", "xcode_archives", regenerable=True)
def _xcode_archives(p):
    return f"{HOME}/Library/Developer/Xcode/Archives/" in p


@rule("dev_artifact", "ios_simulator", regenerable=True)
def _ios_sim(p):
    return f"{HOME}/Library/Developer/CoreSimulator/" in p


@rule("dev_artifact", "rust_target", regenerable=True)
def _rust_target(p):
    return "/target/debug/" in p or "/target/release/" in p


@rule("dev_artifact", "python_venv", regenerable=True)
def _python_venv(p):
    return any(s in p for s in ("/.venv/", "/venv/", "/.tox/")) or "/site-packages/" in p


@rule("dev_artifact", "pycache", regenerable=True)
def _pycache(p):
    return "/__pycache__/" in p or p.endswith(".pyc")


@rule("dev_artifact", "build_dir", regenerable=True)
def _build(p):
    return any(f"/{d}/" in p for d in ("build", "dist", "out", ".next", ".nuxt", ".turbo"))


@rule("dev_artifact", "git_objects", regenerable=True)
def _git(p):
    return "/.git/objects/" in p or "/.git/lfs/" in p


# ── Caches ────────────────────────────────────────────────────────────────────
@rule("cache", "homebrew", regenerable=True)
def _brew(p):
    return f"{HOME}/Library/Caches/Homebrew/" in p


@rule("cache", "npm", regenerable=True)
def _npm(p):
    return f"{HOME}/.npm/" in p


@rule("cache", "yarn", regenerable=True)
def _yarn(p):
    return f"{HOME}/Library/Caches/Yarn/" in p or f"{HOME}/.yarn/cache/" in p


@rule("cache", "pip", regenerable=True)
def _pip(p):
    return f"{HOME}/Library/Caches/pip/" in p


@rule("cache", "cargo_registry", regenerable=True)
def _cargo(p):
    return f"{HOME}/.cargo/registry/" in p


@rule("cache", "go_modules", regenerable=True)
def _go(p):
    return f"{HOME}/go/pkg/mod/cache/" in p


@rule("cache", "browser", regenerable=True)
def _browser(p):
    return any(x in p for x in (
        f"{HOME}/Library/Caches/Google/Chrome/",
        f"{HOME}/Library/Caches/com.apple.Safari/",
        f"{HOME}/Library/Caches/Firefox/",
        f"{HOME}/Library/Application Support/Google/Chrome/Default/Cache/",
        f"{HOME}/Library/Application Support/Google/Chrome/Default/Code Cache/",
    ))


@rule("cache", "chat_app", regenerable=True)
def _chat(p):
    return any(x in p for x in (
        f"{HOME}/Library/Application Support/Slack/Cache/",
        f"{HOME}/Library/Application Support/discord/Cache/",
        f"{HOME}/Library/Application Support/Microsoft/Teams/Cache/",
    ))


@rule("cache", "system_cache", regenerable=True)
def _libcache(p):
    return f"{HOME}/Library/Caches/" in p


# ── Logs ──────────────────────────────────────────────────────────────────────
@rule("logs", "user_log", regenerable=True)
def _log(p):
    return f"{HOME}/Library/Logs/" in p or p.endswith(".log")


# ── iOS / iPad backups (NOT regenerable) ──────────────────────────────────────
@rule("ios_backup", "mobilesync", regenerable=False)
def _ios_backup(p):
    return f"{HOME}/Library/Application Support/MobileSync/" in p


# ── Installers ────────────────────────────────────────────────────────────────
@rule("installer", "package", regenerable=True)
def _installer(p):
    return p.endswith((".dmg", ".pkg"))


# ── Downloads (catch-all for ~/Downloads after specific rules) ────────────────
@rule("downloads", "downloaded", regenerable=False)
def _downloads(p):
    return p.startswith(f"{HOME}/Downloads/")


# ── Media ─────────────────────────────────────────────────────────────────────
@rule("media", "video", regenerable=False)
def _video(p):
    return p.lower().endswith((".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"))


@rule("media", "image", regenerable=False)
def _image(p):
    return p.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".heic", ".raw", ".tiff", ".webp"))


@rule("media", "audio", regenerable=False)
def _audio(p):
    return p.lower().endswith((".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg"))


# ── Documents ─────────────────────────────────────────────────────────────────
@rule("documents", "pdf", regenerable=False)
def _pdf(p):
    return p.lower().endswith(".pdf")


@rule("documents", "office", regenerable=False)
def _office(p):
    return p.lower().endswith((".doc", ".docx", ".pages", ".key", ".numbers", ".xls", ".xlsx", ".ppt", ".pptx"))


@rule("documents", "archive", regenerable=False)
def _archive(p):
    return p.lower().endswith((".zip", ".tar", ".tar.gz", ".tgz", ".rar", ".7z"))


# ── ML model weights ──────────────────────────────────────────────────────────
@rule("ml_model", "weights", regenerable=False)
def _model(p):
    return p.lower().endswith((".gguf", ".safetensors", ".ckpt", ".pt", ".pth", ".onnx", ".bin"))


# ── Apps (treated as opaque bundles) ──────────────────────────────────────────
@rule("apps", "app_bundle", regenerable=False)
def _app(p):
    return p.endswith(".app")


def classify(path):
    for predicate, category, subcategory, regenerable in RULES:
        if predicate(path):
            return category, subcategory, regenerable
    return "other", None, False
