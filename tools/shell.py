import config
import terminal

_DANGEROUS = ["rm -rf", "mkfs", "shutdown", "reboot", ":(){", "dd if=", "sudo rm", "> /dev/sd",
              "del /s", "del /f", "rmdir /s", "rd /s", "format ", "diskpart", "reg delete", "taskkill /f /im"]


def run_shell(command: str, input_text: str = "", timeout: int = 0) -> str:
    """Run a command in a real terminal process inside the project folder and observe its output. Use it to run programs, tests, linters, type checkers, pip install, git, etc. Output is shown live to the user. Each call starts a fresh shell (cmd.exe on Windows), so chain with && (for example: cd sub && python app.py). If the program asks for input, the call returns "waiting for input" with a process id: answer with send_input. If it is a server or watcher (or takes too long) it keeps running in the background and returns its process id.

    Args:
        command: The command to run.
        input_text: Optional text to feed to the program's stdin up front, one answer per line, after which stdin is closed. Use it to test programs that call input().
        timeout: Max seconds to wait before returning (default 180). On timeout the process keeps running and its id is returned.
    """
    if config.CONFIRM_DANGEROUS and any(d in command.lower() for d in _DANGEROUS):
        if input(f"\n  risky command: {command}\n  allow? (y/N): ").strip().lower() != "y":
            return "DENIED by user. Use a safer approach."
    return terminal.run(command, input_text, timeout)


def send_input(process_id: int, text: str, press_enter: bool = True, wait_seconds: int = 3) -> str:
    """Type text into a running program's stdin (answer a prompt, enter a command in a REPL) and return the output that follows.

    Args:
        process_id: Id returned by run_shell.
        text: Text to send.
        press_enter: Append a newline after the text.
        wait_seconds: How long to wait for the program to respond.
    """
    return terminal.send(process_id, text, press_enter, wait_seconds)


def read_output(process_id: int, wait_seconds: int = 5) -> str:
    """Get new output from a background process (server logs, a long task) and see whether it has exited.

    Args:
        process_id: Id returned by run_shell.
        wait_seconds: How long to wait for new output before returning.
    """
    return terminal.read(process_id, wait_seconds)


def stop_process(process_id: int) -> str:
    """Stop a background process (and its child processes). Always stop servers and watchers you started when you are done with them.

    Args:
        process_id: Id returned by run_shell.
    """
    return terminal.stop(process_id)


def list_processes() -> str:
    """List all processes started in this session and whether they are still running."""
    return terminal.list_all()
