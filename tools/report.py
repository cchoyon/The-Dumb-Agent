import session
import terminal


def session_report() -> str:
    """Return a factual log of this debugging session: every command run with its exit status and error line, every file changed with line counts (test files are flagged), and the latest pass/fail status of each command. Call it before writing the final report of a debug task and base the report on it.
    """
    terminal.sync()
    return session.build_report()
