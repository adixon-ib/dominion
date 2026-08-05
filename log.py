from __future__ import annotations

import logging
import os
import sys

VERBOSE = 8
VERBOSE_EX = 6

logging.addLevelName(VERBOSE, "VERBOSE")
logging.addLevelName(VERBOSE_EX, "VERBOSE_EX")

LEVEL_MAP = {
    "off": logging.CRITICAL + 1,
    "info": logging.INFO,
    "debug": logging.DEBUG,
    "verbose": VERBOSE,
    "verbose-ex": VERBOSE_EX,
}


class NiosLogger(logging.Logger):

    def verbose(self, msg: str, *args, **kwargs) -> None:
        if self.isEnabledFor(VERBOSE):
            self._log(VERBOSE, msg, args, **kwargs)

    def verbose_ex(self, msg: str, variables: dict | None = None, **kwargs) -> None:
        if self.isEnabledFor(VERBOSE_EX):
            if variables:
                dump = " | ".join(f"{k}={v!r}" for k, v in variables.items())
                msg = f"{msg}  [{dump}]"
            self._log(VERBOSE_EX, msg, (), **kwargs)


logging.setLoggerClass(NiosLogger)


class InfoFormatter(logging.Formatter):
    def format(self, record):
        return record.getMessage()


class DebugFormatter(logging.Formatter):
    def format(self, record):
        if record.levelno >= logging.INFO:
            return record.getMessage()
        return f"[nios_test] {record.getMessage()}"


class VerboseFormatter(logging.Formatter):
    def __init__(self):
        super().__init__(
            fmt="%(asctime)s %(pathname)s:%(lineno)d  %(message)s",
            datefmt="%H:%M:%S",
        )


_FORMATTER_FOR_LEVEL = {
    "off": InfoFormatter(),
    "info": InfoFormatter(),
    "debug": DebugFormatter(),
    "verbose": VerboseFormatter(),
    "verbose-ex": VerboseFormatter(),
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


def _setup_root_file_handler(log_file: str, level: str) -> None:
    root = logging.getLogger("nios_test")
    for h in root.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(log_file):
            return
    root.setLevel(VERBOSE_EX)
    fh = logging.FileHandler(log_file)
    fh.setLevel(VERBOSE_EX)
    fh.setFormatter(_FORMATTER_FOR_LEVEL[level])
    root.addHandler(fh)


def create_logger(
    name: str = "nios_test",
    level: str = "info",
    log_file: str | None = None,
) -> NiosLogger:
    if level not in LEVEL_MAP:
        raise ValueError(f"Unknown level {level!r}, expected one of {list(LEVEL_MAP)}")

    logger = logging.getLogger(name)
    logger.setLevel(LEVEL_MAP[level])
    logger.handlers.clear()

    formatter = _FORMATTER_FOR_LEVEL[level]

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file:
        _setup_root_file_handler(log_file, level)

    logger.propagate = True
    return logger
