"""
Process manager: runs commands as real subprocesses, shows their output live in your terminal,
lets the agent send stdin, and keeps long-running programs (servers, watchers) alive in the
background. Plain module (not a tool file) so processes survive tool hot-reloads.
"""
import atexit
import codecs
import os
import re
import signal
import subprocess
import threading
import time

import config
import session
import ui
from ui import DIM, RESET

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

procs = {}          # id -> Proc
_next_id = [1]
streamed = False   # True when output was shown live during the current tool call


def _oem_codec() -> str:
    """Windows console programs (dir, ping, ...) print in the OEM code page, not UTF-8."""
    if os.name != "nt":
        return "utf-8"
    try:
        import ctypes
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "cp437"


_OEM = _oem_codec()


def _decode(dec, data: bytes) -> str:
    """UTF-8 first (Python/Node/Git output); if the bytes are not UTF-8, use the console code page."""
    try:
        return dec.decode(data)
    except UnicodeDecodeError:
        dec.reset()
        try:
            return data.decode(_OEM)
        except (UnicodeDecodeError, LookupError):
            return data.decode("utf-8", errors="replace")


class Proc:
    def __init__(self, command: str):
        self.id = _next_id[0]
        _next_id[0] += 1
        self.command = command
        self.start = time.time()
        self.lock = threading.Lock()
        self.buf = ""            # all output so far (stdout + stderr, in order)
        self.read_pos = 0        # how much the agent has already been given
        self.print_pos = 0       # how much was already shown live to the user
        self.live_lines = 0
        self.last_out = self.start
        self.reader_done = False
        self.finished = False
        self.entry = session.start_command(command)

        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                   NO_COLOR="1", TERM="dumb")
        kw = {} if os.name == "nt" else {"start_new_session": True}
        self.p = subprocess.Popen(command, shell=True, cwd=config.WORKDIR, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, bufsize=0, **kw)
        threading.Thread(target=self._reader, daemon=True).start()
        procs[self.id] = self

    # ---- output
    def _reader(self):
        dec = codecs.getincrementaldecoder("utf-8")(errors="strict")
        try:
            while True:
                data = self.p.stdout.read(4096)
                if not data:
                    break
                text = _decode(dec, data)
                text = _ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
                if not text:
                    continue
                with self.lock:
                    self.buf += text
                    self.last_out = time.time()
                    if len(self.buf) > 1_000_000:           # keep memory bounded
                        cut = len(self.buf) - 500_000
                        self.buf = self.buf[cut:]
                        self.read_pos = max(0, self.read_pos - cut)
                        self.print_pos = max(0, self.print_pos - cut)
        except (OSError, ValueError):
            pass
        finally:
            self.reader_done = True

    def new_output(self) -> str:
        with self.lock:
            out = self.buf[self.read_pos:]
            self.read_pos = len(self.buf)
        return out

    # ---- input
    def write(self, text: str) -> bool:
        try:
            self.p.stdin.write(text.encode("utf-8"))
            self.last_out = time.time()      # input counts as activity (resets prompt detection)
            return True
        except (OSError, ValueError):
            return False

    def close_stdin(self):
        try:
            self.p.stdin.close()
        except (OSError, ValueError):
            pass

    # ---- lifecycle
    def running(self) -> bool:
        return self.p.poll() is None

    def kill(self):
        if self.p.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.p.pid)],
                               capture_output=True)
            else:
                os.killpg(os.getpgid(self.p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self.p.kill()
            except OSError:
                pass
        try:
            self.p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass

    def finish(self, stopped: bool = False) -> int:
        """Record the final result in the session log (once). Returns repeated-error count."""
        if self.finished:
            return 0
        self.finished = True
        code = "stopped" if stopped else self.p.returncode
        with self.lock:
            full = self.buf
        return session.finish_command(self.entry, code, full)


# ------------------------------------------------------------------ live display
def _flush_live(proc: Proc, final: bool = False):
    """Print not-yet-shown output to the user's terminal, dimmed, as it arrives."""
    global streamed
    with proc.lock:
        text = proc.buf[proc.print_pos:]
        if not text:
            return
        if not final:
            cut = text.rfind("\n")
            if cut == -1:          # wait for a full line (unless this is the final flush)
                return
            text = text[:cut + 1]
        proc.print_pos += len(text)
    for line in text.splitlines():
        if proc.live_lines < config.LIVE_MAX_LINES:
            print(f"{DIM}  {ui.BAR} {line[:200]}{RESET}", flush=True)
            streamed = True
        elif proc.live_lines == config.LIVE_MAX_LINES:
            print(f"{DIM}  {ui.BAR} {ui.ELL} output continues (not shown live){RESET}", flush=True)
            streamed = True
        proc.live_lines += 1


def wait(proc: Proc, timeout: float, idle=None) -> str:
    """Block until the process exits, asks for input, goes quiet, or the timeout passes.
    Returns "exited" | "prompt" | "idle" | "timeout"."""
    idle = config.IDLE_SECONDS if idle is None else idle
    deadline = time.time() + timeout
    while True:
        _flush_live(proc)
        now = time.time()
        if proc.p.poll() is not None:
            if proc.reader_done or now - proc.last_out > 0.5:
                reason = "exited"
                break
        else:
            quiet = now - proc.last_out
            with proc.lock:
                tail_open = bool(proc.buf) and not proc.buf.endswith("\n")
            if tail_open and quiet >= config.PROMPT_QUIET:
                reason = "prompt"      # output ends mid-line and went quiet: probably "Name: "
                break
            if quiet >= idle:
                reason = "idle"
                break
        if now >= deadline:
            reason = "timeout"
            break
        time.sleep(0.05)
    _flush_live(proc, final=True)
    return reason


def _result(proc: Proc, reason: str) -> str:
    out = proc.new_output().strip("\n")
    elapsed = time.time() - proc.start
    if reason == "exited":
        seen = proc.finish()
        note = ""
        if seen >= 3:
            note = (f"\n\nNOTE: this same error has now occurred {seen} times. Stop repeating the "
                    "same kind of fix: re-read the root cause, try a genuinely different "
                    "approach, or report the blocker.")
        return f"exit {proc.p.returncode} · {elapsed:.1f}s\n{out or '(no output)'}{note}"

    heads = {
        "prompt": f"waiting for input · process {proc.id} · output ends with a prompt",
        "idle": f"running · process {proc.id} · no output for {config.IDLE_SECONDS}s "
                "(waiting for input, a server/watcher, or a slow task)",
        "timeout": f"running · process {proc.id} · still running after {elapsed:.0f}s",
    }
    hint = (f"hint: send_input({proc.id}, '<text>') if it needs input · read_output({proc.id}) "
            f"for more output · stop_process({proc.id}) when you are done with it")
    return f"{heads[reason]}\n{out or '(no new output)'}\n\n{hint}"


# ------------------------------------------------------------------- public API
def get(pid) -> Proc:
    try:
        return procs[int(pid)]
    except (KeyError, ValueError, TypeError):
        raise ValueError(f"no process with id {pid}. Use list_processes.")


def run(command: str, input_text: str = "", timeout: int = 0) -> str:
    global streamed
    streamed = False
    proc = Proc(command)
    if input_text:
        proc.write(input_text if input_text.endswith("\n") else input_text + "\n")
        proc.close_stdin()
    try:
        reason = wait(proc, min(timeout or config.CMD_TIMEOUT, 1800))
    except KeyboardInterrupt:
        proc.kill()
        proc.finish(stopped=True)
        raise
    return _result(proc, reason)


def read(pid, wait_seconds: int = 5) -> str:
    global streamed
    streamed = False
    proc = get(pid)
    return _result(proc, wait(proc, min(max(wait_seconds, 0), 300)))


def send(pid, text: str, press_enter: bool = True, wait_seconds: int = 3) -> str:
    global streamed
    streamed = False
    proc = get(pid)
    if not proc.running():
        return _result(proc, "exited")
    if not proc.write(text + ("\n" if press_enter else "")):
        return "ERROR: could not write to the process (stdin closed)"
    return _result(proc, wait(proc, min(max(wait_seconds, 0), 300)))


def stop(pid) -> str:
    global streamed
    streamed = False
    proc = get(pid)
    was_running = proc.running()
    proc.kill()
    time.sleep(0.2)
    _flush_live(proc, final=True)
    out = proc.new_output().strip("\n")
    proc.finish(stopped=was_running)
    state = "stopped" if was_running else f"already exited (code {proc.p.returncode})"
    return f"{state}\n{out or '(no new output)'}"


def list_all() -> str:
    if not procs:
        return "no processes"
    rows = []
    for p in procs.values():
        state = "running" if p.running() else f"exited {p.p.returncode}"
        rows.append(f"{p.id} · {state} · {time.time() - p.start:.0f}s · {p.command[:80]}")
    return "\n".join(rows)


def sync():
    """Record processes that exited on their own since the agent last looked."""
    for p in procs.values():
        if not p.running() and not p.finished:
            p.finish()


def kill_all():
    for p in list(procs.values()):
        p.kill()


atexit.register(kill_all)
