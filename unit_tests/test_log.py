"""Unit tests for log.py: resolve_log_file, create_logger, NiosLogger levels."""

import logging

import pytest

from nios_test import create_logger, resolve_log_file
from nios_test.src.log import (
    VERBOSE,
    VERBOSE_EX,
    DebugFormatter,
    InfoFormatter,
    VerboseFormatter,
)

# -- resolve_log_file() ---------------------------------------------------------

def test_resolve_log_file_explicit_wins():
    result = resolve_log_file(
        "name", log_file="/explicit/path.log", output_dir="/other",
    )
    assert result == "/explicit/path.log"


def test_resolve_log_file_from_output_dir(tmp_path):
    result = resolve_log_file("myname", output_dir=str(tmp_path))
    assert result == str(tmp_path / "myname.log")


def test_resolve_log_file_creates_output_dir(tmp_path):
    target = tmp_path / "nested" / "dir"
    resolve_log_file("myname", output_dir=str(target))
    assert target.is_dir()


def test_resolve_log_file_neither_given_returns_none():
    assert resolve_log_file("myname") is None


# -- create_logger() ------------------------------------------------------------

def test_create_logger_rejects_unknown_level():
    with pytest.raises(ValueError, match="Unknown level"):
        create_logger(name="nios_test.unit.bad_level", level="loud")


@pytest.mark.parametrize(("level", "expected_level", "formatter_cls"), [
    ("info", logging.INFO, InfoFormatter),
    ("debug", logging.DEBUG, DebugFormatter),
    ("verbose", VERBOSE, VerboseFormatter),
    ("verbose-ex", VERBOSE_EX, VerboseFormatter),
])
def test_create_logger_sets_level_and_formatter(level, expected_level, formatter_cls):
    logger = create_logger(name=f"nios_test.unit.level_{level}", level=level)
    # The logger itself stays maximally permissive (VERBOSE_EX) so a file
    # handler can always capture full detail regardless of console
    # verbosity -- the requested `level` is enforced by the console
    # handler instead.
    assert logger.handlers[0].level == expected_level
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, formatter_cls)


def test_create_logger_off_level_is_above_critical():
    logger = create_logger(name="nios_test.unit.off", level="off")
    assert logger.handlers[0].level > logging.CRITICAL


def test_create_logger_with_log_file_adds_file_handler(tmp_path):
    log_file = str(tmp_path / "run.log")
    logger = create_logger(name="nios_test.unit.with_file", level="info", log_file=log_file)
    file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert any(h.baseFilename == log_file for h in file_handlers)


def test_create_logger_file_handler_is_scoped_to_its_own_logger(tmp_path):
    """Two loggers with distinct log files must not see each other's
    FileHandler -- each logger owns its own handler, not a shared
    ancestor, so records from one never fan out into the other's file."""
    log_file_a = str(tmp_path / "a.log")
    log_file_b = str(tmp_path / "b.log")
    logger_a = create_logger(name="nios_test.unit.scope_a", level="info", log_file=log_file_a)
    logger_b = create_logger(name="nios_test.unit.scope_b", level="info", log_file=log_file_b)

    logger_a.info("from a")
    logger_b.info("from b")

    with open(log_file_a) as f:
        content_a = f.read()
    with open(log_file_b) as f:
        content_b = f.read()

    assert "from a" in content_a
    assert "from b" not in content_a
    assert "from b" in content_b
    assert "from a" not in content_b


def test_create_logger_clears_previous_handlers():
    name = "nios_test.unit.reused"
    create_logger(name=name, level="info")
    logger = create_logger(name=name, level="debug")
    assert len(logger.handlers) == 1


# -- NiosLogger.verbose / verbose_ex ---------------------------------------------

def test_nioslogger_verbose_suppressed_below_threshold():
    logger = create_logger(name="nios_test.unit.verbose_suppressed", level="info")
    records = []
    logger.handlers[0].emit = records.append
    logger.verbose("should not appear")
    assert records == []


def test_nioslogger_verbose_emitted_when_enabled():
    logger = create_logger(name="nios_test.unit.verbose_emitted", level="verbose")
    records = []
    logger.handlers[0].emit = records.append
    logger.verbose("hello %s", "world")
    assert len(records) == 1
    assert records[0].getMessage() == "hello world"


def test_nioslogger_verbose_ex_renders_variables():
    logger = create_logger(name="nios_test.unit.verbose_ex_vars", level="verbose-ex")
    records = []
    logger.handlers[0].emit = records.append
    logger.verbose_ex("context", variables={"a": 1, "b": "two"})
    assert len(records) == 1
    msg = records[0].getMessage()
    assert "context" in msg
    assert "a=1" in msg
    assert "b='two'" in msg
