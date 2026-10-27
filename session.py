"""
Shared state for one debugging session: commands run, files changed, test-file protection.
Plain module (not a tool file). Tools import it with `import session`.
"""
import difflib
import os
import re

import config

commands = []    # [{"cmd", "code", "err"}]
originals = {}   # relative path -> original text (None = file did not exist)

_TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$",
    re.I)


def reset():
    commands.clear()
    originals.clear()


# ---------------------------------------------------------------- paths
def safe_path(path: str) -> str:
    root = os.path.abspath(config.WORKDIR)
    full = os.path.abspath(os.path.join(root, path))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("path must stay inside the project folder")
    return full


def rel(full: str) -> str:
    return os.path.relpath(full, config.WORKDIR).replace(os.sep, "/")


# ------------------------------------------------------------- recording
def start_command(cmd: str) -> dict:
    """Record a command as running (code None). finish_command fills in the result."""
    entry = {"cmd": cmd, "code": None, "err": ""}
    commands.append(entry)
    return entry


def finish_command(entry: dict, code, output: str) -> int:
    """Set the result. code: int exit code, or "stopped" (killed by the agent on purpose).
    Returns how many times this same error line has now been seen."""
    err = ""
    if code not in (0, "stopped"):
        lines = [l.strip() for l in output.splitlines() if l.strip()]
        err = lines[-1][:160] if lines else ""
    entry["code"] = code
    entry["err"] = err
    return sum(1 for c in commands if err and c["err"] == err)


def log_command(cmd: str, code, output: str) -> int:
    return finish_command(start_command(cmd), code, output)


def snapshot(rel_path: str, old_text) -> None:
    """Remember the original content the first time a file is modified."""
    originals.setdefault(rel_path, old_text)


# ------------------------------------------------------- test protection
def is_test_path(rel_path: str) -> bool:
    return bool(_TEST_RE.search(rel_path.replace(os.sep, "/")))


def _stats(old, new):
    sm = difflib.SequenceMatcher(None, (old or "").splitlines(), (new or "").splitlines(),
                                 autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            removed += i2 - i1
        if tag in ("insert", "replace"):
            added += j2 - j1
    return added, removed


def approve_test_change(rel_path: str, old, new) -> bool:
    """Adding to tests is free. Changing/removing existing test lines needs the user's OK."""
    if not config.PROTECT_TESTS or old is None or not is_test_path(rel_path):
        return True
    _, removed = _stats(old, new)
    if removed == 0:
        return True
    ans = input(f"\n  agent wants to change/remove {removed} existing line(s) in test file "
                f"{rel_path}\n  allow? (y/N): ")
    return ans.strip().lower() == "y"


# ----------------------------------------------------------------- report
def build_report() -> str:
    if not commands and not originals:
        return "nothing recorded yet (no commands run, no files changed)"
    out = []
    failed = sum(1 for c in commands if c["code"] not in (0, None, "stopped"))
    out.append(f"commands: {len(commands)} run, {failed} failed")
    start = max(0, len(commands) - 40)
    if start:
        out.append(f"  (showing last 40 of {len(commands)})")
    for i, c in enumerate(commands[start:], start + 1):
        code = c["code"]
        status = ("running" if code is None else "stopped by agent" if code == "stopped"
                  else "ok" if code == 0 else f"exit {code}")
        out.append(f"  {i}. [{status}] {c['cmd'][:100]}")
        if c["err"]:
            out.append(f"       {c['err']}")

    out.append("changes:")
    if not originals:
        out.append("  (no files changed)")
    for rp, old in originals.items():
        try:
            with open(safe_path(rp), encoding="utf-8", newline="") as f:
                new = f.read()
        except OSError:
            out.append(f"  {rp}: (unreadable)")
            continue
        added, removed = _stats(old, new)
        tag = ""
        if is_test_path(rp):
            if old is None:
                tag = " [new test file]"
            elif removed:
                tag = f" [!] existing test lines changed/removed: {removed} (user-approved)"
            else:
                tag = " [existing test file, additions only]"
        label = "new file" if old is None else "modified"
        out.append(f"  {rp}: {label}, +{added} -{removed}{tag}")

    latest = {}
    for c in commands:
        latest.pop(c["cmd"], None)
        latest[c["cmd"]] = c["code"]
    out.append("latest status per command:")
    for cmd, code in list(latest.items())[-15:]:
        label = ("RUNNING" if code is None else "STOPPED" if code == "stopped"
                 else "PASS" if code == 0 else "FAIL")
        out.append(f"  [{label}] {cmd[:100]}")
    return "\n".join(out)
