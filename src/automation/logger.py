"""Tiny console logger with optional ANSI colors.

ASCII markers are used on purpose so Windows terminals without UTF-8 support
still render readable output.
"""

from __future__ import annotations

import logging
import os
import sys

_RESET = "\033[0m"
_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}
_MARKERS = {
    "DEBUG": "»",
    "INFO": "+",
    "WARNING": "!",
    "ERROR": "x",
    "CRITICAL": "!!",
}


def _supports_color(enabled: bool) -> bool:
    if not enabled:
        return False
    if os.getenv("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def configure_logging(level: str, color: bool = True) -> logging.Logger:
    """Configure the root logger and return the application logger."""
    use_color = _supports_color(color)

    class ConsoleFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            marker = _MARKERS.get(record.levelname, "-")
            prefix = f"[{record.name}]"
            head = f"{marker} {prefix} {record.getMessage()}"
            if use_color and record.levelname in _COLORS:
                head = f"{_COLORS[record.levelname]}{head}{_RESET}"
            if record.exc_info:
                head = f"{head}\n{self.formatException(record.exc_info)}"
            return head

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(ConsoleFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))

    # Keep third-party chatter out of the output.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    return logging.getLogger("gitlab-automation")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


__all__ = ["configure_logging", "get_logger"]
