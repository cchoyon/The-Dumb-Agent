import fnmatch
import os
import re

import session

_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build",
         ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode", "target"}


def _walk(root, max_depth=None):
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP and not d.endswith(".egg-info"))
        if max_depth is not None and dirpath.rstrip(os.sep).count(os.sep) - base >= max_depth:
            dirs[:] = []
        yield dirpath, sorted(files)


def list_files(path: str = ".", max_depth: int = 4) -> str:
    """List the project's files (skips .git, node_modules, virtualenvs and caches). Use it to understand the project layout.

    Args:
        path: Folder to list, relative to the project root.
        max_depth: How many folder levels deep to go.
    """
    root = session.safe_path(path)
    found = [session.rel(os.path.join(d, f)) for d, files in _walk(root, max_depth) for f in files]
    if not found:
        return "(no files)"
    shown = found[:300]
    extra = f"\n... +{len(found) - 300} more" if len(found) > 300 else ""
    return "\n".join(shown) + extra


def search_code(pattern: str, path: str = ".", file_glob: str = "") -> str:
    """Search file contents with a regex (like grep). Returns file:line: text. Use it to find where a function, error message or variable is used.

    Args:
        pattern: Regex (or plain text) to search for.
        path: Folder to search, relative to the project root.
        file_glob: Optional filename filter such as "*.py".
    """
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(re.escape(pattern))
    hits = []
    for dirpath, files in _walk(session.safe_path(path)):
        for name in files:
            if file_glob and not fnmatch.fnmatch(name, file_glob):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > 1_000_000:
                    continue
                with open(full, "r", encoding="utf-8") as f:
                    for n, line in enumerate(f, 1):
                        if rx.search(line):
                            hits.append(f"{session.rel(full)}:{n}: {line.strip()[:160]}")
                            if len(hits) >= 60:
                                return "\n".join(hits) + "\n... (first 60 matches)"
            except (UnicodeDecodeError, OSError):
                continue
    return "\n".join(hits) or "no matches"
