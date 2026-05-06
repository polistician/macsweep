"""Resolve a path / bundle ID into a human label + 1-line reason.

Resolution ladder (cheap → expensive):
  1. SQLite labels cache       — instant, signature-keyed, shared across users
  2. apps_index by bundle ID   — populated at scan time from /Applications
  3. Pattern table             — known framework dirs (npm, Cargo, pip, …)
  4. mdls fallback             — Spotlight display name + kind
  5. LLM batch                 — last resort for the long tail
"""
import os
import time
from typing import Optional

from . import db, native


# ── Pattern table — stable across Macs, doesn't need an LLM ─────────────────
PATTERNS = [
    # (signature_prefix, display_name, category, reason)
    ("path:/.Trash",                "Trash",          "trash",        "Items you moved to Trash. Permanently deleting frees the space."),
    ("path:/Library/Caches/Homebrew", "Homebrew cache", "cache",      "Downloaded formula archives. Brew re-downloads on next install."),
    ("path:/.npm/_cacache",         "npm cache",      "cache",        "Package tarballs. npm re-fetches on next install."),
    ("path:/.npm",                  "npm",            "cache",        "Node package manager state. Rebuilt on next install."),
    ("path:/.yarn/cache",           "Yarn cache",     "cache",        "Yarn package archives. Yarn re-fetches on next install."),
    ("path:/.cargo/registry",       "Cargo registry", "cache",        "Rust crate sources. Cargo re-downloads on next build."),
    ("path:/go/pkg/mod/cache",      "Go modules",     "cache",        "Go module sources. go re-downloads on next build."),
    ("path:/Library/Caches/pip",    "pip cache",      "cache",        "Python wheels. pip re-downloads on next install."),
    ("path:/Library/Caches/Yarn",   "Yarn cache",     "cache",        "Yarn package archives. Yarn re-fetches on next install."),
    ("path:/Library/Logs",          "App logs",       "log",          "Application diagnostic logs. Safe to clear."),
    ("path:/Library/Logs/CrashReporter", "Crash reports", "log",      "Records of past app crashes. Not needed for normal use."),
    ("path:/Library/Logs/DiagnosticReports", "System diagnostics", "log", "macOS diagnostic dumps. Safe to remove."),
    ("path:/Library/Application Support/MobileSync", "iOS device backups", "ios_backup", "Local backups of your iOS devices. Verify before removing."),
    ("path:/Library/Developer/Xcode/DerivedData", "Xcode build cache", "dev_artifact", "Intermediate Xcode build files. Regenerated on next build."),
    ("path:/Library/Developer/Xcode/Archives", "Xcode archives", "dev_artifact", "Built app archives. Keep if you need to re-distribute."),
    ("path:/Library/Developer/CoreSimulator", "iOS Simulator", "dev_artifact", "Simulator runtimes and device data. Reinstalled when needed."),
    ("path:/.cache",                "User cache",     "cache",        "Generic application cache. Apps rebuild as needed."),
    ("ext:.dmg",                    "Disk image",     "installer",    "App installer. Already installed apps don't need it."),
    ("ext:.pkg",                    "Installer pkg",  "installer",    "macOS installer package. Safe to delete after install."),
    ("ext:.iso",                    "Disk image",     "installer",    "Bootable disk image. Keep only if you need to re-install."),
    # Plain-name folders that show up in caches but aren't bundle IDs
    ("path:/BraveSoftware",         "Brave Browser",  "browser_cache","Brave's cache and profile. Clearing keeps your bookmarks."),
    ("path:/CocoaPods",             "CocoaPods",      "dev_artifact", "iOS dependency cache. Re-fetched on next pod install."),
    ("path:/ms-playwright",         "Playwright browsers", "dev_artifact", "Test browser binaries. Re-downloaded by playwright install."),
    ("path:/discord",               "Discord",        "app_cache",    "Discord cache. Discord rebuilds it as needed."),
    ("path:/Slack",                 "Slack",          "app_cache",    "Slack cache. Slack rebuilds it as needed."),
    ("path:/SiriTTS",               "Siri voices",    "system_junk",  "Downloaded Siri voice data."),
    ("path:/Code Cache",            "Code cache",     "browser_cache","Compiled JavaScript cache. Browsers rebuild as needed."),
    ("path:/Cache_Data",            "Cache data",     "cache",        "Generic cached files."),
    ("path:/GPUCache",              "GPU cache",      "cache",        "GPU shader cache. Rebuilt by the app on demand."),
]


# ── Bundle-ID prefixes that indicate browser data (preserved logins!) ───────
SENSITIVE_BUNDLE_PREFIXES = ("com.google.Chrome", "org.mozilla.firefox", "com.brave.Browser",
                              "com.apple.Safari", "company.thebrowser.Browser")


# Generic words inside bundle IDs that aren't the app name itself
GENERIC_BUNDLE_WORDS = {"app", "client", "desktop", "mac", "macos", "osx", "ios",
                         "helper", "framework", "service", "agent"}


def _prettify_bundle(bundle_id: str) -> str:
    """Best-guess display name from a bundle ID. com.spotify.client → Spotify."""
    parts = [p for p in bundle_id.split(".") if p]
    # Strip TLD-style prefixes and generic suffixes
    while parts and parts[0].lower() in {"com", "org", "net", "io", "ru", "de", "uk", "co"}:
        parts.pop(0)
    while parts and parts[-1].lower() in GENERIC_BUNDLE_WORDS:
        parts.pop()
    if not parts:
        return bundle_id
    # Take the longest remaining segment (often the actual app name)
    candidate = max(parts, key=len)
    return candidate.replace("-", " ").replace("_", " ").title()


def signature_for(path: str, hint: Optional[str] = None) -> str:
    """Stable signature key. Same on every Mac for the same artifact."""
    if hint:
        return hint
    home = str(os.path.expanduser("~"))
    rel = path.replace(home, "~", 1) if path.startswith(home) else path

    # Bundle-ID-like directory names (e.g. com.spotify.client, ru.keepcoder.Telegram).
    # Allow mixed case but reject anything with spaces or special chars.
    parts = rel.strip("/").split("/")
    for part in parts:
        if (part.count(".") >= 2
                and " " not in part
                and not part.startswith(".")
                and part[0].isalpha()):
            return f"bundle:{part}"

    # Pattern matches use the path prefix
    for sig_prefix, *_ in PATTERNS:
        if sig_prefix.startswith("path:"):
            marker = sig_prefix[5:]
            if marker in path:
                return sig_prefix
        elif sig_prefix.startswith("ext:"):
            ext = sig_prefix[4:]
            if path.lower().endswith(ext):
                return sig_prefix

    # Fall back to the basename
    return f"path:{os.path.basename(path) or rel}"


def _from_cache(conn, signature: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT name, category, reason, source, confidence, model FROM labels WHERE signature = ?",
        (signature,),
    ).fetchone()
    if not row:
        return None
    conn.execute("UPDATE labels SET hits = hits + 1 WHERE signature = ?", (signature,))
    return dict(row)


def _from_apps_index(conn, signature: str) -> Optional[dict]:
    """If signature looks like 'bundle:com.foo.bar', look up display name."""
    if not signature.startswith("bundle:"):
        return None
    bundle_id = signature[7:]
    row = conn.execute(
        "SELECT display_name FROM apps_index WHERE bundle_id = ?",
        (bundle_id,),
    ).fetchone()
    if not row:
        # Orphan: bundle-id-shaped folder but no installed app. Prettify the
        # bundle ID — pick the most meaningful segment.
        return {
            "name": f"{_prettify_bundle(bundle_id)} (orphan)",
            "category": "leftover",
            "reason": "Cache for an app no longer installed. Safe to remove.",
            "source": "bundle_orphan",
            "confidence": "medium",
        }
    name = row["display_name"]
    # Sensitive: browser/login-state apps
    if any(bundle_id.startswith(p) for p in SENSITIVE_BUNDLE_PREFIXES):
        return {
            "name": f"{name} cache",
            "category": "browser_cache",
            "reason": f"{name}'s temporary cache. Clearing keeps your bookmarks and passwords.",
            "source": "apps_index",
            "confidence": "high",
        }
    return {
        "name": f"{name} cache",
        "category": "app_cache",
        "reason": f"{name}'s temporary cache. {name} will rebuild it as needed.",
        "source": "apps_index",
        "confidence": "high",
    }


def _from_pattern(signature: str) -> Optional[dict]:
    for sig_prefix, name, category, reason in PATTERNS:
        if signature == sig_prefix or signature.startswith(sig_prefix):
            return {
                "name": name, "category": category, "reason": reason,
                "source": "pattern", "confidence": "high",
            }
    return None


def _from_mdls(path: str) -> Optional[dict]:
    if not os.path.lexists(path):
        return None
    name = native.mdls_display_name(path)
    kind = native.mdls_kind(path)
    if not name and not kind:
        return None
    return {
        "name": name or os.path.basename(path) or "Unknown",
        "category": "other",
        "reason": kind or "Spotlight item.",
        "source": "mdls",
        "confidence": "medium",
    }


def resolve_label(path: str, signature: Optional[str] = None) -> dict:
    """Resolve via the ladder. Returns dict with name/category/reason/source/confidence."""
    sig = signature or signature_for(path)
    with db.connect() as conn:
        cached = _from_cache(conn, sig)
        if cached:
            return {"signature": sig, **cached}

        for resolver in (
            lambda: _from_apps_index(conn, sig),
            lambda: _from_pattern(sig),
            lambda: _from_mdls(path),
        ):
            result = resolver()
            if result:
                # Cache it
                conn.execute(
                    """INSERT OR REPLACE INTO labels
                       (signature, name, category, reason, confidence, source, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (sig, result["name"], result["category"], result["reason"],
                     result.get("confidence", "high"), result["source"], time.time()),
                )
                return {"signature": sig, **result}

    # No native resolution. Return placeholder; LLM batch can later overwrite.
    return {
        "signature": sig,
        "name": os.path.basename(path) or sig,
        "category": "other",
        "reason": "Unknown item.",
        "source": "unknown",
        "confidence": "low",
    }


def warm_apps_index() -> int:
    """Populate apps_index from mdfind. Call once per scan."""
    apps = native.installed_apps()
    if not apps:
        return 0
    now = time.time()
    with db.connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO apps_index
               (bundle_id, display_name, path, version, last_used_date, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(a["bundle_id"], a["display_name"], a["path"], a.get("version"),
              None, now) for a in apps],
        )
    return len(apps)


def upsert_label(signature: str, name: str, category: str, reason: str,
                 confidence: str = "high", source: str = "rule", model: Optional[str] = None) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO labels
               (signature, name, category, reason, confidence, source, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (signature, name, category, reason, confidence, source, model, time.time()),
        )
