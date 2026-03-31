"""Async logging for MW4Agent: queue-based, non-blocking, multi-target (console, file, log host).

Usage:
  from mw4agent.log import setup_logging, get_logger

  setup_logging()  # call once at process start (e.g. in CLI main)
  logger = get_logger(__name__)
  logger.info("message")

Configuration (env):
  MW4AGENT_LOG_LEVEL       - DEBUG|INFO|WARNING|ERROR (default: INFO)
  MW4AGENT_LOG_CONSOLE     - 1|0|true|false  enable stderr (default: 1)
  MW4AGENT_LOG_FILE         - path to log file; if set, enables file logging with rotation
  MW4AGENT_LOG_FILE_MAX_BYTES - max bytes per file (default: 10485760 = 10MB)
  MW4AGENT_LOG_FILE_BACKUP_COUNT - number of backup files (default: 5)
  MW4AGENT_LOG_HOST        - host:port for TCP log host (e.g. 127.0.0.1:9020)
  MW4AGENT_LOG_FORMAT      - optional format string
  MW4AGENT_LOG_AGENT_COLORS - 1|0 : color ``[agent:id]`` prefixes in LLM INFO logs when stderr is a TTY (default: 1). Honors NO_COLOR.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler, SocketHandler
from typing import Optional

# Default format
_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_listener: Optional[QueueListener] = None
_log_queue: Optional[queue.Queue] = None


def _parse_level(name: str) -> int:
    return getattr(logging, (name or "INFO").upper(), logging.INFO)


def _build_handlers() -> list:
    handlers = []
    level = _parse_level(os.environ.get("MW4AGENT_LOG_LEVEL", "INFO"))
    fmt_str = os.environ.get("MW4AGENT_LOG_FORMAT", _DEFAULT_FORMAT)
    formatter = logging.Formatter(fmt_str, datefmt=_DEFAULT_DATE_FORMAT)

    # Console (stderr)
    console_raw = os.environ.get("MW4AGENT_LOG_CONSOLE", "1")
    if str(console_raw).strip().lower() in ("1", "true", "yes", "on"):
        h = logging.StreamHandler()
        h.setLevel(level)
        h.setFormatter(formatter)
        handlers.append(h)

    # File (rotating)
    log_file = os.environ.get("MW4AGENT_LOG_FILE", "").strip()
    if log_file:
        try:
            max_bytes = int(os.environ.get("MW4AGENT_LOG_FILE_MAX_BYTES", "10485760"))
        except ValueError:
            max_bytes = 10 * 1024 * 1024
        try:
            backup_count = int(os.environ.get("MW4AGENT_LOG_FILE_BACKUP_COUNT", "5"))
        except ValueError:
            backup_count = 5
        try:
            fh = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            fh.setLevel(level)
            fh.setFormatter(formatter)
            handlers.append(fh)
        except OSError:
            pass  # skip file if not writable

    # Log host (TCP socket)
    log_host = os.environ.get("MW4AGENT_LOG_HOST", "").strip()
    if log_host and ":" in log_host:
        host, _, port_str = log_host.rpartition(":")
        try:
            port = int(port_str)
            sh = SocketHandler(host.strip(), port)
            sh.setLevel(level)
            sh.setFormatter(formatter)
            handlers.append(sh)
        except (ValueError, OSError):
            pass

    return handlers


def setup_logging(
    *,
    level: Optional[str] = None,
    console: Optional[bool] = None,
    log_file: Optional[str] = None,
    log_host: Optional[str] = None,
) -> None:
    """Configure async logging: records are put on a queue, a background thread emits to handlers.

    Call once at process start. Overrides for level/console/file/host take precedence over env.
    """
    global _listener, _log_queue

    if _listener is not None:
        return  # already set up

    if level is not None:
        os.environ["MW4AGENT_LOG_LEVEL"] = level
    if console is not None:
        os.environ["MW4AGENT_LOG_CONSOLE"] = "1" if console else "0"
    if log_file is not None:
        os.environ["MW4AGENT_LOG_FILE"] = log_file
    if log_host is not None:
        os.environ["MW4AGENT_LOG_HOST"] = log_host

    handlers = _build_handlers()
    if not handlers:
        return

    _log_queue = queue.Queue(-1)  # unbounded
    queue_handler = QueueHandler(_log_queue)
    root = logging.getLogger()
    root.setLevel(_parse_level(os.environ.get("MW4AGENT_LOG_LEVEL", "INFO")))
    root.addHandler(queue_handler)
    # Prevent propagation to avoid duplicate handling if parent is configured
    root.propagate = False

    _listener = QueueListener(_log_queue, *handlers, respect_handler_level=True)
    _listener.start()


def stop_logging() -> None:
    """Stop the queue listener (e.g. on process exit)."""
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given name. Use after setup_logging() for async output."""
    return logging.getLogger(name)
