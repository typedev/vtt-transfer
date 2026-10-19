import sys

__version__ = "0.1.0"


def setup_console() -> None:
    """Console output is not ASCII (✓, ·, →, —). Redirected to a file or pipe, Windows encodes it in the
    ANSI code page and print() raises; write UTF-8 there, and never fail on a character."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
