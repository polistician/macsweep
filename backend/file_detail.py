"""File-level metadata + preview + open/reveal actions.

Pulls native macOS metadata that the index doesn't store: creation time
(birthtime), the download source URL stored in `kMDItemWhereFroms` xattr,
and Spotlight content-type info via `mdls`. Preview thumbnails are produced
by macOS's own `qlmanage` so they look like Finder's Quick Look.
"""
import mimetypes
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

QL_CACHE = Path(tempfile.gettempdir()) / "macsweep-ql"
QL_CACHE.mkdir(exist_ok=True)


def detail(path: str) -> dict:
    """Return the full info card for a single file."""
    if not os.path.lexists(path):
        return {"path": path, "exists": False}

    try:
        st = os.lstat(path)
    except OSError as e:
        return {"path": path, "exists": False, "error": str(e)}

    is_dir = os.path.isdir(path) and not os.path.islink(path)

    info = {
        "path": path,
        "name": os.path.basename(path),
        "size": st.st_size if not is_dir else _dir_size(path),
        "is_dir": is_dir,
        "is_symlink": os.path.islink(path),
        "mtime": st.st_mtime,
        "atime": st.st_atime,
        "ctime": st.st_ctime,
        "btime": getattr(st, "st_birthtime", st.st_ctime),
        "ext": os.path.splitext(path)[1].lower().lstrip("."),
        "mime": _mime(path),
        "kind": _kind(path),
        "source_urls": _where_from(path),
        "exists": True,
        "parent": os.path.dirname(path),
        "file_command": file_command(path),  # helps explain extension-less binaries
    }
    return info


def _dir_size(path: str) -> int:
    """Actual disk usage (matches `du`)."""
    total = 0
    for dp, _, fns in os.walk(path, followlinks=False, onerror=lambda _e: None):
        for f in fns:
            try:
                st = os.lstat(os.path.join(dp, f))
                blocks = getattr(st, "st_blocks", None)
                total += blocks * 512 if blocks is not None else st.st_size
            except OSError:
                pass
    return total


def _mime(path: str) -> str | None:
    m, _ = mimetypes.guess_type(path)
    return m


def _kind(path: str) -> str:
    """Full file-type taxonomy. Returns one of:
    image, video, audio, pdf, ebook, archive, installer, document, spreadsheet,
    presentation, text, code, model, app, font, design, threed, executable,
    dir, system, other.
    """
    if os.path.isdir(path) and not os.path.islink(path):
        if path.endswith(".app"):
            return "app"
        return "dir"
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return KIND_BY_EXT.get(ext, "other")


# Comprehensive ext → kind map.
KIND_BY_EXT = {
    # image
    "jpg": "image", "jpeg": "image", "png": "image", "gif": "image",
    "heic": "image", "heif": "image", "raw": "image", "tiff": "image",
    "tif": "image", "webp": "image", "bmp": "image", "svg": "image",
    "ico": "image", "icns": "image", "psd": "image", "ai": "image",
    "cr2": "image", "nef": "image", "arw": "image", "dng": "image",
    # video
    "mp4": "video", "mov": "video", "mkv": "video", "avi": "video",
    "webm": "video", "m4v": "video", "wmv": "video", "flv": "video",
    "mpg": "video", "mpeg": "video", "3gp": "video", "mts": "video",
    "m2ts": "video", "ts": "video",
    # audio
    "mp3": "audio", "m4a": "audio", "wav": "audio", "flac": "audio",
    "aac": "audio", "ogg": "audio", "opus": "audio", "wma": "audio",
    "aiff": "audio", "aif": "audio", "caf": "audio", "midi": "audio", "mid": "audio",
    # documents
    "pdf": "pdf",
    "doc": "document", "docx": "document", "pages": "document", "rtf": "document",
    "odt": "document", "wpd": "document",
    "xls": "spreadsheet", "xlsx": "spreadsheet", "numbers": "spreadsheet",
    "ods": "spreadsheet", "csv": "spreadsheet", "tsv": "spreadsheet",
    "ppt": "presentation", "pptx": "presentation", "key": "presentation",
    "odp": "presentation",
    # ebooks
    "epub": "ebook", "mobi": "ebook", "azw": "ebook", "azw3": "ebook", "fb2": "ebook",
    # archive
    "zip": "archive", "tar": "archive", "gz": "archive", "tgz": "archive",
    "rar": "archive", "7z": "archive", "bz2": "archive", "xz": "archive",
    "lz": "archive", "lzma": "archive", "z": "archive", "cab": "archive",
    "deb": "archive", "rpm": "archive", "apk": "archive", "jar": "archive",
    "war": "archive", "ear": "archive",
    # installer / disk image
    "dmg": "installer", "pkg": "installer", "iso": "installer",
    "img": "installer", "vhd": "installer", "vmdk": "installer",
    # text
    "txt": "text", "md": "text", "rst": "text", "tex": "text", "log": "text",
    "ini": "text", "conf": "text", "cfg": "text", "toml": "text",
    "json": "text", "xml": "text", "yaml": "text", "yml": "text", "plist": "text",
    "html": "text", "htm": "text", "css": "text", "scss": "text", "sass": "text",
    "less": "text", "env": "text",
    # code
    "py": "code", "js": "code", "mjs": "code", "cjs": "code", "ts": "code",
    "tsx": "code", "jsx": "code", "vue": "code", "svelte": "code",
    "go": "code", "rs": "code", "java": "code", "kt": "code", "scala": "code",
    "c": "code", "cpp": "code", "cc": "code", "cxx": "code", "h": "code", "hpp": "code",
    "m": "code", "mm": "code", "swift": "code", "rb": "code", "php": "code",
    "pl": "code", "lua": "code", "sh": "code", "bash": "code", "zsh": "code",
    "fish": "code", "ps1": "code", "bat": "code", "cmd": "code",
    "sql": "code", "r": "code", "jl": "code", "ex": "code", "exs": "code",
    "erl": "code", "hs": "code", "clj": "code", "elm": "code", "dart": "code",
    "f90": "code", "fpp": "code", "asm": "code", "s": "code",
    # ml / model weights
    "gguf": "model", "safetensors": "model", "ckpt": "model", "pt": "model",
    "pth": "model", "onnx": "model", "bin": "model", "h5": "model", "tflite": "model",
    "mlmodel": "model", "pb": "model", "joblib": "model", "pickle": "model", "pkl": "model",
    # design / vector / 3D
    "sketch": "design", "fig": "design", "xd": "design", "afdesign": "design",
    "afphoto": "design", "indd": "design", "idml": "design",
    "blend": "threed", "obj": "threed", "fbx": "threed", "stl": "threed",
    "dae": "threed", "3ds": "threed", "max": "threed", "ma": "threed", "mb": "threed",
    "gltf": "threed", "glb": "threed", "usdz": "threed",
    # font
    "ttf": "font", "otf": "font", "woff": "font", "woff2": "font", "eot": "font",
    # executable / binary
    "exe": "executable", "msi": "executable", "appimage": "executable",
    "so": "executable", "dylib": "executable", "dll": "executable", "a": "executable",
    "lib": "executable", "o": "executable", "obj": "executable",
    "wasm": "executable",
    # system
    "ds_store": "system", "lock": "system", "swp": "system", "tmp": "system",
}


def _where_from(path: str) -> list[str]:
    """Read the kMDItemWhereFroms xattr → list of source URLs (download origins)."""
    try:
        out = subprocess.run(
            ["xattr", "-px", "com.apple.metadata:kMDItemWhereFroms", path],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return []
        # `xattr -px` returns hex; convert to bytes then plist-parse.
        hex_str = out.stdout.replace(" ", "").replace("\n", "")
        data = bytes.fromhex(hex_str)
        urls = plistlib.loads(data)
        if isinstance(urls, list):
            return [str(u) for u in urls if u]
    except Exception:
        pass
    return []


def preview(path: str, size: int = 512) -> Path | None:
    """Generate a Quick Look thumbnail. Returns the path to a PNG, or None."""
    if not os.path.lexists(path):
        return None
    # Cache key
    safe = abs(hash((path, size, int(os.path.getmtime(path)) if os.path.exists(path) else 0)))
    cache_path = QL_CACHE / f"{safe}.png"
    if cache_path.exists():
        return cache_path

    # qlmanage writes <name>.png into the output dir
    out_dir = QL_CACHE / f"_{safe}"
    out_dir.mkdir(exist_ok=True)
    try:
        subprocess.run(
            ["qlmanage", "-t", "-s", str(size), "-o", str(out_dir), path],
            capture_output=True, timeout=10,
        )
        for f in out_dir.iterdir():
            if f.suffix == ".png":
                f.rename(cache_path)
                break
    except Exception:
        return None
    finally:
        # Clean up empty dir
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass
    return cache_path if cache_path.exists() else None


def open_in_finder(path: str) -> bool:
    """Reveal in Finder."""
    if not os.path.lexists(path):
        return False
    subprocess.Popen(["open", "-R", path])
    return True


def open_default(path: str) -> bool:
    """Open with the default app."""
    if not os.path.lexists(path):
        return False
    subprocess.Popen(["open", path])
    return True


def quicklook(path: str) -> bool:
    """Open the native macOS Quick Look preview window. Same UX as Spacebar in Finder."""
    if not os.path.lexists(path):
        return False
    # qlmanage -p shows the real Quick Look window. The process is detached
    # so we don't wait for the user to close it.
    subprocess.Popen(
        ["qlmanage", "-p", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return True


def file_command(path: str) -> str | None:
    """Run `file` to identify a binary's true type. Helps with extension-less files."""
    if not os.path.lexists(path):
        return None
    try:
        out = subprocess.run(
            ["file", "-b", path],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip()[:200]
    except Exception:
        return None
