"""structlog setup. JSON in prod, console-pretty in dev.

CRITICAL: structlog output MUST flow through Python's stdlib logging so the
RotatingFileHandler attached to the root logger catches it. With the previous
PrintLoggerFactory, structlog wrote directly to stdout and bypassed rotation —
over a 7-day run, logs/combined.log would grow unbounded.

The plumbing:
  structlog event → processors (incl. final renderer) → stdlib logger.info(str)
  stdlib root logger has RotatingFileHandler(10MB × 5) attached
  stdlib formatter is %(message)s (renderer already produced the full line)
"""
from __future__ import annotations
import logging
import logging.handlers
import sys
from pathlib import Path

import structlog


def configure_logging(level: str = "INFO", fmt: str = "console", log_file: Path = None) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        # LoggerFactory routes log calls through stdlib `logging` instead of
        # printing directly to stdout. This is what lets RotatingFileHandler
        # actually see and rotate our log lines.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib root: RotatingFileHandler + a stream handler so the
    # respawn wrapper's console log still captures something useful if rotation
    # ever lags (and so foreground runs print to terminal).
    root = logging.getLogger()
    root.setLevel(log_level)

    # Idempotent: avoid stacking handlers if configure_logging is called twice.
    for h in list(root.handlers):
        root.removeHandler(h)

    # The renderer already produced the full formatted line, so use %(message)s.
    formatter = logging.Formatter("%(message)s")

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_file), maxBytes=10 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    else:
        # No rotated file requested — fall back to stdout so logs aren't lost.
        # Production never takes this branch; tests + foreground dev runs do.
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)
