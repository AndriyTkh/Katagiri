"""Shared-file logging: creation, level, idempotence, and failing safe.

Every test here runs against a tmp ``%LOCALAPPDATA%`` and restores the
``katagiri`` logger afterwards. Both halves matter: the logger is a process-wide
singleton, and ``tests/test_skeleton.py`` asserts that every handler on it is a
stderr ``StreamHandler``. A file handler left behind by this module would fail
that test in a way that looks nothing like its cause.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import pytest

from katagiri import applog
from katagiri import config as config_mod


@pytest.fixture
def clean_logger():
    """Restore the ``katagiri`` logger exactly as it was found.

    Handlers are closed as well as removed: on Windows an open file handle in a
    tmp directory blocks pytest's own cleanup of that directory.
    """
    logger = logging.getLogger(applog.LOGGER_NAME)
    saved = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    yield logger
    for handler in list(logger.handlers):
        if handler not in saved:
            logger.removeHandler(handler)
            handler.close()
    logger.handlers[:] = saved
    logger.setLevel(saved_level)
    logger.propagate = saved_propagate


@pytest.fixture
def app_data(tmp_path, monkeypatch):
    """Point %LOCALAPPDATA% at a tmp dir; the log follows config.toml's home."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(applog.LEVEL_ENV_VAR, raising=False)
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


def _file_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if isinstance(h, logging.FileHandler)]


def test_log_path_sits_beside_config_toml(app_data):
    assert applog.log_file_path() == app_data / "Katagiri" / "logs" / "katagiri.log"


def test_setup_creates_the_log_file_and_a_line_lands_in_it(app_data, clean_logger):
    log_path = applog.log_file_path()
    assert not log_path.exists()

    logger = applog.setup_logging()

    # The directory is made on demand and the file exists before anything is
    # logged: writability is proven at setup, not at the first emit.
    assert log_path.is_file()

    applog.get_logger("test_applog").info("hello-from-the-file-log")
    for handler in logger.handlers:
        handler.flush()

    assert "hello-from-the-file-log" in log_path.read_text(encoding="utf-8")


def test_logged_line_carries_time_level_and_logger_name(app_data, clean_logger):
    logger = applog.setup_logging()
    applog.get_logger("shapecheck").warning("a-marked-line")
    for handler in logger.handlers:
        handler.flush()

    line = next(
        entry
        for entry in applog.log_file_path().read_text(encoding="utf-8").splitlines()
        if "a-marked-line" in entry
    )
    assert "WARNING" in line
    assert "katagiri.shapecheck" in line
    # A timestamp, not just a message: "2026-08-20 12:00:00,123 ..."
    assert line[:4].isdigit()


def test_handler_is_a_rotating_handler_with_the_expected_limits(app_data, clean_logger):
    logger = applog.setup_logging()
    handlers = _file_handlers(logger)

    assert len(handlers) == 1
    handler = handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == applog.MAX_BYTES == 5 * 1024 * 1024
    assert handler.backupCount == applog.BACKUP_COUNT == 3
    assert handler.encoding == "utf-8"


def test_default_level_is_info_and_debug_is_dropped(app_data, clean_logger):
    logger = applog.setup_logging()
    child = applog.get_logger("levels")
    child.debug("debug-must-not-appear")
    child.info("info-must-appear")
    for handler in logger.handlers:
        handler.flush()

    text = applog.log_file_path().read_text(encoding="utf-8")
    assert "info-must-appear" in text
    assert "debug-must-not-appear" not in text


def test_env_var_raises_verbosity_to_debug(app_data, clean_logger, monkeypatch):
    monkeypatch.setenv(applog.LEVEL_ENV_VAR, "DEBUG")

    logger = applog.setup_logging()

    assert logger.level == logging.DEBUG
    applog.get_logger("levels").debug("debug-now-appears")
    for handler in logger.handlers:
        handler.flush()
    assert "debug-now-appears" in applog.log_file_path().read_text(encoding="utf-8")


def test_unparseable_env_level_falls_back_instead_of_raising(app_data, monkeypatch):
    monkeypatch.setenv(applog.LEVEL_ENV_VAR, "chatty")
    assert applog.log_level() == logging.INFO
    monkeypatch.setenv(applog.LEVEL_ENV_VAR, "10")
    assert applog.log_level() == logging.DEBUG


def test_setup_twice_does_not_duplicate_handlers(app_data, clean_logger):
    first = applog.setup_logging()
    count = len(first.handlers)
    file_handler = _file_handlers(first)[0]

    second = applog.setup_logging()

    assert second is first
    assert len(second.handlers) == count
    assert _file_handlers(second) == [file_handler], "the same handler, not a new one"


def test_no_handler_writes_to_stdout(app_data, clean_logger):
    """stdout is the MCP JSON-RPC wire; nothing this module adds may touch it."""
    import sys

    logger = applog.setup_logging()
    for handler in logger.handlers:
        stream = getattr(handler, "stream", None)
        assert stream is not sys.stdout
        assert stream is not sys.__stdout__
    assert not logger.propagate, "root may own a stdout handler we do not control"


def test_unwritable_log_dir_degrades_to_a_null_handler(app_data, clean_logger, capsys):
    """A file where the logs directory belongs: setup must return, not raise."""
    (app_data / "Katagiri").mkdir(parents=True)
    (app_data / "Katagiri" / "logs").write_text("not a directory", encoding="utf-8")

    logger = applog.setup_logging()  # must not raise

    assert _file_handlers(logger) == []
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)

    # And logging still works, silently, with nothing on stdout.
    applog.get_logger("degraded").info("dropped-on-the-floor")
    assert capsys.readouterr().out == ""


def test_missing_localappdata_degrades_instead_of_raising(monkeypatch, clean_logger):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv(applog.LEVEL_ENV_VAR, raising=False)
    config_mod.reset_config_cache()
    try:
        logger = applog.setup_logging()  # must not raise ConfigError
        assert _file_handlers(logger) == []
        assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)
    finally:
        config_mod.reset_config_cache()


def test_truncated_repr_bounds_untrusted_text():
    short = applog.truncated_repr("abc")
    assert short == "'abc'"

    long_text = applog.truncated_repr("x" * 5000)
    assert len(long_text) < 600
    assert "truncated" in long_text
    assert applog.DEBUG_REPR_LIMIT == 500


def test_truncated_repr_survives_an_exploding_repr():
    class Hostile:
        def __repr__(self):
            raise ValueError("no repr for you")

    text = applog.truncated_repr(Hostile())

    assert "Hostile" in text
    assert "ValueError" in text


def test_exception_summary_keeps_only_the_first_line():
    """Pydantic spells rejected arguments into the *body* of its message.

    That body is untrusted tool input, and INFO lines must not carry it.
    """
    exc = ValueError(
        "1 validation error for lookupArguments\n"
        "surface\n"
        "  Field required [type=missing, input_value={'word': 'secret'}]"
    )

    summary = applog.exception_summary(exc)

    assert summary == "ValueError: 1 validation error for lookupArguments"
    assert "input_value" not in summary
    assert "secret" not in summary


def test_exception_summary_bounds_a_long_first_line():
    summary = applog.exception_summary(RuntimeError("y" * 4000))
    assert len(summary) < applog.ERROR_SUMMARY_LIMIT + 60
    assert summary.startswith("RuntimeError: ")
    assert summary.endswith("...")


def test_run_cli_logs_start_and_finish(app_data, clean_logger):
    code = applog.run_cli("fake_cli", lambda: 0)

    assert code == 0
    for handler in logging.getLogger(applog.LOGGER_NAME).handlers:
        handler.flush()
    text = applog.log_file_path().read_text(encoding="utf-8")
    assert "fake_cli starting" in text
    assert "fake_cli finished" in text
    assert "exit code 0" in text


def test_run_cli_logs_and_reraises_a_failure(app_data, clean_logger):
    def boom() -> int:
        raise RuntimeError("the collection is locked")

    with pytest.raises(RuntimeError):
        applog.run_cli("fake_cli", boom)

    for handler in logging.getLogger(applog.LOGGER_NAME).handlers:
        handler.flush()
    text = applog.log_file_path().read_text(encoding="utf-8")
    assert "fake_cli failed" in text
    assert "RuntimeError: the collection is locked" in text
    assert "exit code" not in text, "a failed run must not read as a clean finish"
    # The message, not a traceback: stderr is shared with the MCP wire.
    assert "Traceback (most recent call last)" not in text
