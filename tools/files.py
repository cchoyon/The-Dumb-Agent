import os

import session

_DENIED = ("DENIED by user: existing test content must not be changed or removed to make tests "
           "pass. Fix the source code instead (adding new tests is allowed).")


def read_file(path: str) -> str:
    """Read a text file from the project folder.

    Args:
        path: Relative file path.
    """
    with open(session.safe_path(path), "r", encoding="utf-8") as f:
        return f.read()


def write_file(path: str, content: str) -> str:
    """Create a new file, or fully overwrite one. For fixing existing code prefer edit_file, which makes the smallest change.

    Args:
        path: Relative file path, e.g. "tests/test_calc.py".
        content: The full file content.
    """
    full = session.safe_path(path)
    rel = session.rel(full)
    old = None
    if os.path.exists(full):
        with open(full, "r", encoding="utf-8", newline="") as f:
            old = f.read()
    if not session.approve_test_change(rel, old, content):
        return _DENIED
    session.snapshot(rel, old)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return f"wrote {rel} ({len(content.splitlines())} lines)"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace one exact piece of text in an existing file. This is the preferred way to fix code: it keeps the change small. old_text must match exactly once, so include a few surrounding lines if needed.

    Args:
        path: Relative file path.
        old_text: The exact existing text to replace (whitespace matters).
        new_text: The replacement text.
    """
    full = session.safe_path(path)
    rel = session.rel(full)
    with open(full, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    if "\r\n" in text:  # keep Windows line endings working
        old_text = old_text.replace("\r\n", "\n").replace("\n", "\r\n")
        new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
    if not old_text:
        return "ERROR: old_text is empty"
    n = text.count(old_text)
    if n == 0:
        return "ERROR: old_text not found. read_file the file and copy the text exactly."
    if n > 1:
        return f"ERROR: old_text matches {n} places. Add more surrounding lines to make it unique."
    new = text.replace(old_text, new_text, 1)
    if not session.approve_test_change(rel, text, new):
        return _DENIED
    session.snapshot(rel, text)
    with open(full, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    return f"edited {rel} (-{len(old_text.splitlines())} +{len(new_text.splitlines())} lines)"
