"""
Model providers behind one interface:  llm.chat(messages, tools) -> {"content", "tool_calls"}

  Ollama  runs on your own machine (private, nothing leaves it). Uses Ollama's native /api/chat
          so the context window (num_ctx) can be set. Standard library only, no extra packages.
  Groq    cloud API (your prompts and files are sent to Groq). Needs `pip install groq`.

The agent keeps its history in OpenAI-style messages; the Ollama adapter converts them.
"""
import getpass
import itertools
import json
import os
import re
import socket
import urllib.error
import urllib.request

import config
import ui

_call_ids = itertools.count(1)


class LLMError(Exception):
    """fatal=True means retrying will not help (bad key, model missing, server down)."""

    def __init__(self, msg, fatal=False):
        super().__init__(msg)
        self.fatal = fatal


# ================================================================= helpers
def normalize_host(host) -> str:
    h = (host or "").strip() or "127.0.0.1:11434"
    if "://" not in h:
        h = "http://" + h
    h = h.replace("0.0.0.0", "127.0.0.1").rstrip("/")
    m = re.match(r"^(https?://)([^/:]+)(:\d+)?", h)
    if m and not m.group(3) and m.group(1) == "http://":
        h = f"http://{m.group(2)}:11434"
    return h


def _is_local(host: str) -> bool:
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:|/|$)", host))


def _http_json(url, body=None, timeout=30, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (agent-framework)"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", "replace"))
            detail = detail.get("error", detail) if isinstance(detail, dict) else detail
            if isinstance(detail, dict):
                detail = detail.get("message", "")
        except Exception:
            detail = ""
        err = LLMError(f"HTTP {e.code}: {detail or e.reason}", fatal=e.code in (400, 401, 403, 404))
        err.code = e.code
        raise err
    except (socket.timeout, TimeoutError):
        raise LLMError(f"no answer within {timeout}s (model still loading, or too slow for this PC)")
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise LLMError(f"no answer within {timeout}s (model still loading, or too slow for this PC)")
        raise LLMError(f"cannot connect to {url.split('/api')[0].split('/openai')[0]} ({e.reason})", fatal=True)


# ---- Ollama message conversion (OpenAI-style history -> Ollama native)
def _to_ollama(messages):
    out, names = [], {}
    for m in messages:
        role = m["role"]
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc["function"]
                names[tc["id"]] = fn["name"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                calls.append({"function": {"name": fn["name"], "arguments": args}})
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            out.append({"role": "tool", "tool_name": names.get(m.get("tool_call_id"), ""),
                        "content": m.get("content", "")})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def _salvage_tool_calls(content, tool_names):
    """Some local models print the tool call as JSON text instead of a structured call."""
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    if not text.startswith(("{", "[")):
        return []
    try:
        obj = json.loads(text)
    except ValueError:
        return []
    calls = []
    for item in (obj if isinstance(obj, list) else [obj]):
        if not isinstance(item, dict):
            return []
        fn = item.get("function") if isinstance(item.get("function"), dict) else item
        name = fn.get("name")
        args = fn.get("arguments", fn.get("parameters", {}))
        if name not in tool_names:
            return []
        calls.append({"id": f"call_{next(_call_ids)}", "name": name,
                      "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)})
    return calls


# ================================================================= Ollama
class OllamaLLM:
    provider = "ollama"

    def __init__(self, model, host=None):
        self.model = model
        self.host = normalize_host(host or config.OLLAMA_HOST)
        self.local = _is_local(self.host)
        self.max_context_chars = int(config.OLLAMA_NUM_CTX * 3.2)

    def label(self):
        where = "local, private" if self.local else f"REMOTE {self.host}, not private"
        return f"ollama {ui.SEP} {self.model} ({where})"

    def with_model(self, model):
        return OllamaLLM(model, self.host)

    def chat(self, messages, tools=None):
        body = {"model": self.model, "messages": _to_ollama(messages), "stream": False,
                "options": {"temperature": config.TEMPERATURE, "num_ctx": config.OLLAMA_NUM_CTX,
                            "num_predict": 8000}}
        if tools:
            body["tools"] = tools
        try:
            data = _http_json(self.host + "/api/chat", body, timeout=config.OLLAMA_TIMEOUT)
        except LLMError as e:
            text = str(e).lower()
            if "not found" in text:
                raise LLMError(f"model '{self.model}' is not installed. Run: ollama pull {self.model}", fatal=True)
            if "does not support tools" in text:
                raise LLMError(f"model '{self.model}' cannot call tools, so the agent cannot use it. "
                               "Pick another model with /model.", fatal=True)
            if str(e).startswith("cannot connect"):
                raise LLMError(f"Ollama is not reachable at {self.host}. Start it (open the Ollama app "
                               "or run: ollama serve), then try again.", fatal=True)
            raise
        msg = data.get("message") or {}
        content = msg.get("content") or ""
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments", {})
            calls.append({"id": f"call_{next(_call_ids)}", "name": fn.get("name", ""),
                          "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)})
        if not calls and tools:
            names = {t["function"]["name"] for t in tools}
            calls = _salvage_tool_calls(content, names)
            if calls:
                content = ""
        return {"content": content, "tool_calls": calls}


def list_ollama_models(host):
    """Installed models: [{name, size, remote, tools}]. tools: True/False/None(unknown)."""
    data = _http_json(host + "/api/tags", timeout=8)
    models = []
    for m in data.get("models", []):
        name = m.get("model") or m.get("name")
        if not name:
            continue
        caps = None
        try:
            caps = _http_json(host + "/api/show", {"model": name}, timeout=8).get("capabilities")
        except LLMError:
            pass
        if caps is not None and "completion" not in caps:
            continue                     # embedding-only models cannot chat
        models.append({"name": name, "size": m.get("size", 0),
                       "remote": bool(m.get("remote_host") or m.get("remote_model")) or "cloud" in name.lower(),
                       "tools": None if caps is None else ("tools" in caps)})
    return models


# ================================================================= Groq
class GroqLLM:
    provider = "groq"
    max_context_chars = 300_000

    def __init__(self, api_key, model):
        try:
            from groq import Groq
        except ImportError:
            raise LLMError("The 'groq' package is not installed. Run: pip install groq", fatal=True)
        self.api_key, self.model = api_key, model
        self.client = Groq(api_key=api_key)

    def label(self):
        return f"groq {ui.SEP} {self.model} (cloud: prompts and files are sent to Groq)"

    def with_model(self, model):
        return GroqLLM(self.api_key, model)

    def chat(self, messages, tools=None):
        kw = dict(model=self.model, messages=messages, temperature=config.TEMPERATURE,
                  max_completion_tokens=8000)
        if tools:
            kw.update(tools=tools, tool_choice="auto")
        if config.REASONING_EFFORT and "gpt-oss" in self.model:
            kw["reasoning_effort"] = config.REASONING_EFFORT
        try:
            resp = self.client.chat.completions.create(**kw)
        except Exception as e:
            code = getattr(e, "status_code", None)
            if code == 401:
                raise LLMError("Groq rejected the API key (401). Use /model to enter it again.", fatal=True)
            if code == 404:
                raise LLMError(f"Groq has no model called '{self.model}'. Use /model to pick another.", fatal=True)
            raise LLMError(str(e)[:300], fatal=code in (400, 403) and "tool_use_failed" not in str(e))
        m = resp.choices[0].message
        calls = [{"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"}
                 for tc in (m.tool_calls or [])]
        return {"content": m.content or "", "tool_calls": calls}


def _check_groq_key(key, model):
    """Returns (ok, message). Network problems are reported but never block the user."""
    try:
        data = _http_json("https://api.groq.com/openai/v1/models", timeout=10,
                          headers={"Authorization": f"Bearer {key}"})
    except LLMError as e:
        if getattr(e, "code", None) == 401:
            return False, "Groq says this API key is invalid."
        return True, f"could not verify the key right now ({e}); continuing"
    ids = [m.get("id") for m in data.get("data", [])]
    if ids and model not in ids:
        return True, f"note: '{model}' is not in your key's model list (e.g. {', '.join(ids[:4])})"
    return True, ""


# ================================================================= interactive picker
def _ask(prompt, default=None):
    answer = input(prompt).strip()
    return answer or default


def _fmt_size(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _pick_ollama(host_arg=None, model_arg=None):
    host = normalize_host(host_arg or config.OLLAMA_HOST)
    while True:
        try:
            models = list_ollama_models(host)
        except LLMError as e:
            print(f"{ui.RED}Cannot reach Ollama at {host}: {e}{ui.RESET}")
            print("Start Ollama (open the Ollama app, or run: ollama serve).")
            if (_ask("Press Enter to retry, or type b to go back: ", "") or "").lower() == "b":
                return None
            continue
        if not models:
            print("Ollama is running but has no models yet. Install one, for example:  ollama pull qwen3:8b")
            if (_ask("Press Enter to retry, or type b to go back: ", "") or "").lower() == "b":
                return None
            continue
        break
    if model_arg:
        return OllamaLLM(model_arg, host)
    print(f"\nModels installed in Ollama ({host}):")
    for i, m in enumerate(models, 1):
        tools = {True: f"tools {ui.OK}", False: f"tools {ui.NO} (cannot be used by the agent)",
                 None: "tools ?"}[m["tools"]]
        cloud = "  CLOUD model: not private" if m["remote"] else ""
        size = "cloud" if m["remote"] else _fmt_size(m["size"])
        print(f"  {i}) {m['name']:<32} {size:>9}   {tools}{cloud}")
    names = [m["name"] for m in models]
    while True:
        choice = _ask("Pick a number (or type a model name, b = back): ")
        if not choice:
            continue
        if choice.lower() == "b":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            m = models[int(choice) - 1]
        elif choice in names:
            m = models[names.index(choice)]
        else:
            print("Not in the list above. Install it first with: ollama pull " + choice)
            continue
        if m["tools"] is False and (_ask("This model cannot call tools. Use it anyway? [y/N]: ", "n") or "n").lower() != "y":
            continue
        if m["remote"]:
            print(f"{ui.RED}Warning: this is an Ollama cloud model; your data leaves this machine.{ui.RESET}")
        return OllamaLLM(m["name"], host)


def _pick_groq(model_arg=None):
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if key:
        use = (_ask("Use the GROQ_API_KEY found in your environment? [Y/n]: ", "y") or "y").lower()
        if use != "y":
            key = ""
    while True:
        if not key:
            try:
                key = getpass.getpass("Paste your Groq API key (input is hidden; paste, then press Enter): ").strip()
            except (EOFError, KeyboardInterrupt):
                raise
            if not key:
                print("No key entered.")
                if (_ask("Press Enter to try again, or type b to go back: ", "") or "").lower() == "b":
                    return None
                continue
        model = model_arg or _ask(f"Groq model name [{config.GROQ_DEFAULT_MODEL}]: ", config.GROQ_DEFAULT_MODEL)
        ok, note = _check_groq_key(key, model)
        if note:
            print(f"{ui.DIM}{note}{ui.RESET}")
        if not ok:
            key = ""
            continue
        return GroqLLM(key, model)


def choose_model_interactive(provider=None, model=None, host=None):
    """Ask which provider to use. Returns a ready LLM object (never None)."""
    while True:
        p = provider
        if not p:
            print("\nWhich model do you want to use?")
            print("  1) Ollama  - runs on your PC, private (nothing leaves your machine)")
            print("  2) Groq    - cloud API (your prompts and files are sent to Groq)")
            c = (_ask("Choice [1/2]: ", "") or "").lower()
            p = {"1": "ollama", "ollama": "ollama", "2": "groq", "groq": "groq"}.get(c)
            if not p:
                continue
        llm = _pick_ollama(host, model) if p == "ollama" else _pick_groq(model)
        if llm:
            return llm
        provider = model = None          # user went back: show the full menu again
