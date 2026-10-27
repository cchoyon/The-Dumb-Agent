import importlib.util
import json
import os
import re
import sys

import config

_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", "target"}


def _read(name):
    try:
        with open(os.path.join(config.WORKDIR, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _has(*names):
    return any(os.path.exists(os.path.join(config.WORKDIR, n)) for n in names)


def _py_files():
    """Python files in the first 3 levels of the project."""
    found = []
    base = config.WORKDIR.rstrip(os.sep).count(os.sep)
    for dirpath, dirs, files in os.walk(config.WORKDIR):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        if dirpath.rstrip(os.sep).count(os.sep) - base >= 3:
            dirs[:] = []
        found += [os.path.join(dirpath, f) for f in files if f.endswith(".py")]
        if len(found) > 500:
            break
    return found


def _py_cmd(module, pkg, args):
    cmd = f'"{sys.executable}" -m {module} {args}'.strip()
    if importlib.util.find_spec(module) is None:
        cmd += f"   (not installed: pip install {pkg})"
    return cmd


def detect_checks() -> str:
    """Inspect the project and list the test, lint and type-check commands it appears to use. Call this first when debugging a project.
    """
    res = {"test": [], "lint": [], "types": []}

    # ---- Python
    py = _py_files()
    cfg = "\n".join(_read(n) for n in ("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini",
                                       "mypy.ini", "ruff.toml", ".ruff.toml", "Pipfile"))
    if py:
        names = [os.path.basename(p) for p in py]
        has_tests = any(re.match(r"(test_.*|.*_test)\.py$", n) for n in names) or _has("tests", "test")
        if has_tests or "pytest" in cfg or _has("pytest.ini", "conftest.py"):
            res["test"].append(_py_cmd("pytest", "pytest", "-q"))
        if _has("ruff.toml", ".ruff.toml") or "[tool.ruff" in cfg:
            res["lint"].append(_py_cmd("ruff", "ruff", "check ."))
        if _has(".flake8") or "[flake8]" in cfg:
            res["lint"].append(_py_cmd("flake8", "flake8", ""))
        if _has(".pylintrc") or "[tool.pylint" in cfg:
            res["lint"].append(_py_cmd("pylint", "pylint", "."))
        if _has("mypy.ini") or "[tool.mypy" in cfg or "[mypy" in cfg:
            res["types"].append(_py_cmd("mypy", "mypy", "."))
        if _has("pyrightconfig.json") or "[tool.pyright" in cfg:
            res["types"].append(_py_cmd("pyright", "pyright", ""))

    # ---- Node / TypeScript
    if _has("package.json"):
        try:
            scripts = json.loads(_read("package.json")).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        if scripts.get("test") and "no test specified" not in scripts["test"]:
            res["test"].append("npm test")
        if scripts.get("lint"):
            res["lint"].append("npm run lint")
        typed = [k for k in ("typecheck", "type-check", "check-types", "tsc") if k in scripts]
        if typed:
            res["types"].append(f"npm run {typed[0]}")
        elif _has("tsconfig.json"):
            res["types"].append("npx tsc --noEmit")

    # ---- Go / Rust
    if _has("go.mod"):
        res["test"].append("go test ./...")
        res["lint"].append("go vet ./...")
    if _has("Cargo.toml"):
        res["test"].append("cargo test")
        res["lint"].append("cargo clippy")
        res["types"].append("cargo check")

    # ---- Makefile targets
    if _has("Makefile"):
        mk = _read("Makefile")
        for kind in ("test", "lint"):
            if re.search(rf"^{kind}:", mk, re.M):
                res[kind].append(f"make {kind}")

    if not any(res.values()):
        return ("no test / lint / type-check setup detected. Run the code directly to get a "
                "baseline, and write a minimal test file for the code you fix (regression test).")
    return "\n".join(f"{k}: {' | '.join(v) if v else '(none detected)'}" for k, v in res.items())
