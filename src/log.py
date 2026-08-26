from __future__ import annotations

import logging
import os
import sys

VERBOSE = 8

logging.addLevelName(VERBOSE, "VERBOSE")

LEVEL_MAP = {
    "off": logging.CRITICAL + 1,
    "info": logging.INFO,
    "debug": logging.DEBUG,
    "verbose": VERBOSE,
}


class NiosLogger(logging.Logger):

    def verbose(self, msg: str, *args, **kwargs) -> None:
        if self.isEnabledFor(VERBOSE):
            self._log(VERBOSE, msg, args, **kwargs)


logging.setLoggerClass(NiosLogger)


class InfoFormatter(logging.Formatter):
    def format(self, record):
        return record.getMessage()


class DebugFormatter(logging.Formatter):
    def __init__(self):
        super().__init__(fmt="%(asctime)s  %(message)s", datefmt="%H:%M:%S")


class VerboseFormatter(logging.Formatter):
    def __init__(self):
        super().__init__(
            fmt="%(asctime)s %(filename)s:%(lineno)d  %(message)s",
            datefmt="%H:%M:%S",
        )


_FORMATTER_FOR_LEVEL = {
    "off": InfoFormatter(),
    "info": InfoFormatter(),
    "debug": DebugFormatter(),
    "verbose": VerboseFormatter(),
}


def resolve_log_file(
    name: str,
    log_file: str | None = None,
    output_dir: str | None = None,
) -> str | None:
    if log_file:
        return log_file
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        return os.path.join(output_dir, f"{name}.log")
    return None


def _add_file_handler(logger: logging.Logger, log_file: str, level: str) -> None:
    abspath = os.path.abspath(log_file)
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == abspath:
            return
    fh = logging.FileHandler(log_file)
    fh.setLevel(LEVEL_MAP[level])
    fh.setFormatter(_FORMATTER_FOR_LEVEL[level])
    logger.addHandler(fh)


def create_logger(
    name: str = "dominion",
    level: str = "info",
    log_file: str | None = None,
) -> NiosLogger:
    if level not in LEVEL_MAP:
        raise ValueError(f"Unknown level {level!r}, expected one of {list(LEVEL_MAP)}")

    logger = logging.getLogger(name)
    # Let everything through the logger itself; each handler filters to its
    # own verbosity below.
    logger.setLevel(VERBOSE)
    logger.handlers.clear()

    formatter = _FORMATTER_FOR_LEVEL[level]

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(LEVEL_MAP[level])
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file:
        _add_file_handler(logger, log_file, level)

    logger.propagate = True
    return logger
