import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(level: str | None = None, log_file: str | None = None) -> None:
    """Configure root logger with console and rotating file handler.

    Reads `LOG_LEVEL` and `LOG_FILE` from env if arguments are not provided.
    Safe to call multiple times; subsequent calls will not duplicate handlers if a
    rotating file handler with the same filename already exists.
    """
    level = level or os.environ.get("LOG_LEVEL", "INFO")
    log_file = log_file or os.environ.get("LOG_FILE", "bot_iiko.log")

    root_logger = logging.getLogger()
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ensure there's at least one console handler
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        ch = logging.StreamHandler()
        ch.setLevel(numeric_level)
        ch.setFormatter(formatter)
        root_logger.addHandler(ch)

    # add rotating file handler if not present
    try:
        if log_file and not any(isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', '') == os.path.abspath(log_file) for h in root_logger.handlers):
            fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')
            fh.setLevel(numeric_level)
            fh.setFormatter(formatter)
            root_logger.addHandler(fh)
    except Exception:
        # best-effort: don't fail application if logging file handler cannot be created
        root_logger.debug("Could not create RotatingFileHandler for %s", log_file, exc_info=True)
