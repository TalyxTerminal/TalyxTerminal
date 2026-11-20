"""Canonical local paths and source-tree identity shared by objects, runs and materials."""
import hashlib
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

MODULE = Path(__file__).resolve().parents[1]
EXCLUDED = {".venv", "node_modules", ".git", "__pycache__", "mlruns", "output", "dist"}


def local_path(uri):
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc != "localhost":
            path = "//" + parsed.netloc + path
        elif len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return Path(path).resolve()
    return Path(uri).resolve()


def filesystem_path(path):
    """Use Windows extended paths for IO while keeping ordinary file URIs."""
    absolute = Path(path).resolve()
    value = str(absolute)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return absolute
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def source_identity(root):
    root = filesystem_path(root)
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file() and not any(part in EXCLUDED for part in path.relative_to(root).parts) and path.suffix != ".pyc"}
