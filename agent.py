"""
Core agent. You normally never edit this file.
  - Add a tool  -> drop a .py file with plain functions into tools/
  - Change limits/prompt/Ollama settings -> edit config.py

Run:  python agent.py [project_folder] [--provider ollama|groq] [--model NAME] [--ollama-host URL]
With no options it asks which model to use (Ollama = local/private, Groq = cloud).
Works in Windows CMD, PowerShell, Windows Terminal, Linux and macOS.
"""
import argparse
import importlib.util
import inspect
import json
import os
import re
import sys
import time
import types
import typing
from glob import glob

import ui                      # first: makes the console UTF-8 safe and enables colors
import config
import providers
import session
import terminal
from ui import BOLD, DIM, RED, RESET

TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")

# ---------------------------------------------------------------- tool loading
_JSON_TYPES = {str: "string", int: "integer", float: "number",
               bool: "boolean", list: "array", dict: "object"}
_UNIONS = tuple(x for x in (typing.Union, getattr(types, "UnionType", None)) if x)


def _unwrap(tp):
    """Optional[X] / X | None -> X"""
    if typing.get_origin(tp) in _UNIONS:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return _unwrap(args[0]) if args else str
    return tp


def _json_type(tp):
    tp = _unwrap(tp)
    return _JSON_TYPES.get(typing.get_origin(tp) or tp, "string")


def _array_items(tp):
    args = typing.get_args(_unwrap(tp))
    return {"type": _json_type(args[0]) if args else "string"}


def _parse_doc(fn):
    """Docstring -> (description, {param: description})"""
    desc, params, last, mode = [], {}, None, "desc"
    for raw in (inspect.getdoc(fn) or "").splitlines():
        s = raw.strip()
        head = s.rstrip(":").lower()
        if head in ("args", "arguments", "parameters"):
            mode = "args"
            continue
        if head in ("returns", "return", "raises", "example", "examples"):
            mode = "skip"
            continue
        if mode == "desc":
            if s:
                desc.append(s)
        elif mode == "args" and s:
            m = re.match(r"^\*{0,2}(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$", s)
            if m:
                last = m.group(1)
                params[last] = m.group(2)
            elif last:
                params[last] += " " + s
    return " ".join(desc), params


def build_schema(fn):
    """Python function (type hints + docstring) -> OpenAI/Ollama tool schema."""
    desc, pdocs = _parse_doc(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    props, required = {}, []
    for name, p in inspect.signature(fn).parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        tp = hints.get(name, str)
        prop = {"type": _json_type(tp)}
        if prop["type"] == "array":
            prop["items"] = _array_items(tp)
        if name in pdocs:
            prop["description"] = pdocs[name]
        props[name] = prop
        if p.default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "function", "function": {
        "name": fn.__name__,
        "description": desc or fn.__name__,
        "parameters": {"type": "object", "properties": props, "required": required}}}


def load_tools():
    """Scan tools/*.py. Every public function (no leading underscore) becomes a tool."""
    funcs, schemas = {}, []
    for path in sorted(glob(os.path.join(TOOLS_DIR, "*.py"))):
        mod_name = os.path.basename(path)[:-3]
        if mod_name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"agent_tools_{mod_name}", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"{RED}! tools/{mod_name}.py failed to load: {e}{RESET}")
            continue
        for fn_name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn_name.startswith("_") or fn.__module__ != mod.__name__:
                continue
            funcs[fn_name] = fn
            schemas.append(build_schema(fn))
    return funcs, schemas


def _coerce(args, props):
    """Local models often send "5" for an integer or a JSON string for an object. Fix by schema."""
    out = {}
    for key, v in args.items():
        t = props.get(key, {}).get("type")
        try:
            if t == "integer" and isinstance(v, str):
                v = int(float(v.strip()))
            elif t == "integer" and isinstance(v, float) and v.is_integer():
                v = int(v)
            elif t == "number" and isinstance(v, str):
                v = float(v)
            elif t == "boolean" and isinstance(v, str):
                v = v.strip().lower() in ("true", "1", "yes")
            elif t in ("array", "object") and isinstance(v, str):
                v = json.loads(v)
        except ValueError:
            pass
        out[key] = v
    return out


# --------------------------------------------------------------------- display
def _fmt_call(name, args):
    parts = []
    for k, v in args.items():
        s = str(v)
        if "\n" in s or len(s) > 70:
            parts.append(f"{k}={ui.ELL}")
        else:
            parts.append(s if len(args) == 1 else f"{k}={s}")
    return f"{name}({', '.join(parts)})"


def _show_result(text):
    lines = [l.rstrip() for l in text.strip().splitlines() if l.strip()] or ["(no output)"]
    for i, line in enumerate(lines[:config.PREVIEW_LINES]):
        line = line if len(line) <= 140 else line[:139] + ui.ELL
        print(f"{DIM}  {ui.RES + ' ' if i == 0 else '   '}{line}{RESET}")
    if len(lines) > config.PREVIEW_LINES:
        print(f"{DIM}    {ui.ELL} +{len(lines) - config.PREVIEW_LINES} lines{RESET}")


def _truncate(text):
    n = config.MAX_TOOL_OUTPUT
    if len(text) <= n:
        return text
    return text[: n // 2] + "\n...[truncated]...\n" + text[-n // 2:]


def _trim_history(messages, budget_chars):
    """Keep the conversation inside the model's context window by shrinking OLD tool outputs."""
    def size():
        return sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls", ""))) for m in messages)
    if size() <= budget_chars:
        return
    for m in messages[:-8]:
        if m["role"] == "tool" and len(m["content"]) > 400:
            m["content"] = m["content"][:300] + f"\n{ui.ELL}[older output trimmed to save context]"
            if size() <= budget_chars:
                return


# ----------------------------------------------------------------------- agent
def _call_model(llm, messages, schemas):
    last = None
    for attempt in range(3):
        if ui.COLOR:
            print(f"{DIM}  thinking{ui.ELL}{RESET}", end="", flush=True)
        try:
            return llm.chat(messages, schemas)
        except providers.LLMError as e:
            if e.fatal:
                raise
            last = e
            time.sleep(2 * (attempt + 1))
        finally:
            if ui.COLOR:
                print(ui.CLEAR_LINE, end="", flush=True)
    raise last


def run_agent(llm, messages, funcs, schemas):
    props = {s["function"]["name"]: s["function"]["parameters"].get("properties", {}) for s in schemas}
    schema_chars = len(json.dumps(schemas))
    for _ in range(config.MAX_STEPS):
        _trim_history(messages, max(llm.max_context_chars - schema_chars, 8000))
        msg = _call_model(llm, messages, schemas)

        entry = {"role": "assistant", "content": msg["content"]}
        if msg["tool_calls"]:
            entry["tool_calls"] = [{"id": c["id"], "type": "function",
                                    "function": {"name": c["name"], "arguments": c["arguments"]}}
                                   for c in msg["tool_calls"]]
        messages.append(entry)

        if not msg["tool_calls"]:
            print(f"\n{BOLD}{ui.DOT}{RESET} {msg['content'].strip() or '(the model returned an empty reply)'}\n")
            return

        if msg["content"].strip():
            print(f"{DIM}{msg['content'].strip()}{RESET}")

        for c in msg["tool_calls"]:
            name, bad = c["name"], None
            try:
                args = json.loads(c["arguments"] or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
            except ValueError as e:
                args, bad = {}, f"ERROR: tool arguments were not valid JSON ({e}). Call the tool again with valid JSON."
            args = _coerce(args, props.get(name, {}))
            print(f"{BOLD}{ui.DOT}{RESET} {_fmt_call(name, args)}")
            terminal.streamed = False
            try:
                if bad:
                    out = bad
                else:
                    if name not in funcs:
                        raise KeyError(f"unknown tool '{name}'. Available: {', '.join(funcs)}")
                    out = funcs[name](**args)
                    out = out if isinstance(out, str) else json.dumps(out, default=str, ensure_ascii=False)
            except Exception as e:
                out = f"ERROR: {type(e).__name__}: {e}"
            if terminal.streamed:   # output already shown live: print only the status line
                status = out.strip().splitlines()[0][:140] if out.strip() else ""
                print(f"{DIM}  {ui.RES} {status}{RESET}")
            else:
                _show_result(out)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": _truncate(out)})

    # Step limit hit: force an honest status instead of a silent stop.
    print(f"\n{RED}{ui.DOT} step limit reached{RESET}")
    messages.append({"role": "user", "content": (
        "Step limit reached. Do not call tools. In 3-6 short lines say what is done, what is "
        "still failing, and what is blocking you. Do not claim success unless the last runs passed.")})
    try:
        final = _call_model(llm, messages, [])
        messages.append({"role": "assistant", "content": final["content"]})
        print(f"{BOLD}{ui.DOT}{RESET} {final['content'].strip()}\n")
    except Exception as e:
        print(f"{RED}{ui.DOT} could not get a status report: {e}{RESET}\n")
    print(f"{DIM}type 'continue' to keep going{RESET}\n")


def _clean_path(p):
    return os.path.abspath(os.path.expanduser(p.strip().strip('"').strip("'")))


def _set_workdir(path):
    config.WORKDIR = _clean_path(path)
    os.makedirs(config.WORKDIR, exist_ok=True)


def _banner(llm, nfuncs):
    print(f"\n{BOLD}{llm.label()}{RESET}")
    print(f"{nfuncs} tools {ui.SEP} project: {config.WORKDIR}")
    print(f"{DIM}/debug [focus]  /report  /cd <path>  /model [name]  /tools  /clear  /exit{RESET}\n")


def main():
    ap = argparse.ArgumentParser(description="Local/cloud coding agent")
    ap.add_argument("project", nargs="?", help="project folder the agent works in")
    ap.add_argument("--provider", choices=["ollama", "groq"], help="skip the menu: ollama (local) or groq (cloud)")
    ap.add_argument("--model", help="model name (with --provider)")
    ap.add_argument("--ollama-host", help="Ollama address, default http://127.0.0.1:11434")
    args = ap.parse_args()

    _set_workdir(args.project or config.WORKDIR)
    try:
        llm = providers.choose_model_interactive(args.provider, args.model, args.ollama_host)
    except (EOFError, KeyboardInterrupt):
        print()
        return
    except providers.LLMError as e:
        sys.exit(f"{e}")

    messages = [{"role": "system", "content": config.build_system_prompt()}]
    funcs, schemas = load_tools()
    _banner(llm, len(funcs))

    while True:
        try:
            user = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user in ("/exit", "/quit", "exit"):
            break
        if user == "/clear":
            messages = messages[:1]
            session.reset()
            print(f"{DIM}history cleared{RESET}")
            continue
        if user == "/report":
            terminal.sync()
            print(f"{DIM}{session.build_report()}{RESET}")
            continue
        if user.startswith("/cd"):
            parts = user.split(maxsplit=1)
            if len(parts) == 2:
                terminal.kill_all()
                _set_workdir(parts[1])
                session.reset()
                messages[0]["content"] = config.build_system_prompt()
            print(f"{DIM}project: {config.WORKDIR}{RESET}")
            continue
        if user == "/tools":
            funcs, schemas = load_tools()
            print(f"{DIM}{', '.join(funcs) or '(none)'}{RESET}")
            continue
        if user.startswith("/model"):
            parts = user.split(maxsplit=1)
            try:
                llm = llm.with_model(parts[1].strip()) if len(parts) == 2 else providers.choose_model_interactive()
            except (EOFError, KeyboardInterrupt):
                print()
            except providers.LLMError as e:
                print(f"{RED}{e}{RESET}")
            print(f"{DIM}model: {llm.label()}{RESET}")
            continue
        if user.startswith("/debug"):
            focus = user[len("/debug"):].strip()
            session.reset()
            user = ("Debug this project using the iterative test-and-fix loop (DEBUG WORKFLOW)."
                    + (f" Focus: {focus}" if focus else ""))

        funcs, schemas = load_tools()  # hot reload: new tool files work without restart
        checkpoint = len(messages)
        messages.append({"role": "user", "content": user})
        try:
            run_agent(llm, messages, funcs, schemas)
        except KeyboardInterrupt:
            del messages[checkpoint:]
            print(f"\n{RED}{ui.DOT} interrupted{RESET}\n")
        except providers.LLMError as e:
            del messages[checkpoint:]
            print(f"\n{RED}{ui.DOT} {e}{RESET}\n")
        except Exception as e:
            del messages[checkpoint:]
            print(f"\n{RED}{ui.DOT} error: {type(e).__name__}: {e}{RESET}\n")


if __name__ == "__main__":
    main()
