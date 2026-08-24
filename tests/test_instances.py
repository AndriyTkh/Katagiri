r"""kata-007 T014: side-by-side Katagiri instances (US5, spec.md FR-015..FR-017).

Covers ``KATAGIRI_DATA_HOME``/``KATAGIRI_CONFIG`` resolution in
``katagiri.config`` (T011), the installer's ``--data-home`` flag (T012), and
the ``connection_status`` diagnostic tool (T002, merged into this branch) —
proving the three work together: a second, side-by-side instance gets its own
config/db/log home, the default install is untouched, and two servers backed
by two homes are distinguishable over the wire.

Nothing here touches the real ``%LOCALAPPDATA%\Katagiri`` or this checkout's
own ``agent/.env``: every test either points ``LOCALAPPDATA``/``KATAGIRI_DATA_HOME``
at a ``tmp_path`` sandbox, or (for the installer's env-persistence step)
monkeypatches ``installer._repo_root`` to a throwaway directory so
``_persist_data_home_env`` writes into a sandboxed ``agent/.env`` copy instead
of this worktree's real one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from katagiri import applog
from katagiri import config as config_mod
from katagiri import installer

REPO_ROOT = Path(__file__).resolve().parent.parent

PROTOCOL_VERSION = "2026-07-28"

_GUARDED_ENV_VARS = ("KATAGIRI_DATA_HOME", "KATAGIRI_CONFIG", "LOCALAPPDATA")


@pytest.fixture(autouse=True)
def _isolated_instance_env():
    """Snapshot/restore the three env vars this whole module manipulates.

    Autouse so no test in this file can leak an override into a sibling test
    (or, worse, into the real default home) if it fails partway through.
    """
    saved = {name: os.environ.get(name) for name in _GUARDED_ENV_VARS}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        config_mod.reset_config_cache()


# ---------------------------------------------------------------------------
# katagiri.config: KATAGIRI_DATA_HOME / KATAGIRI_CONFIG resolution (T011)
# ---------------------------------------------------------------------------


def test_data_home_env_var_isolates_config_dir_and_logs_dir_into_fresh_home(tmp_path):
    home = tmp_path / "instance-a"
    os.environ.pop("KATAGIRI_CONFIG", None)
    os.environ["KATAGIRI_DATA_HOME"] = str(home)
    config_mod.reset_config_cache()

    assert config_mod.config_dir() == home
    assert config_mod.config_path() == home / config_mod.CONFIG_FILE_NAME
    assert applog.logs_dir() == home / applog.LOGS_DIR_NAME
    assert applog.log_file_path() == home / applog.LOGS_DIR_NAME / applog.LOG_FILE_NAME

    # Loading actually creates config.toml under the override home, and only
    # there -- nothing lands beside it in tmp_path or anywhere else.
    cfg = config_mod.load_config()
    assert cfg.config_file == home / config_mod.CONFIG_FILE_NAME
    assert cfg.config_file.exists()
    assert list(tmp_path.iterdir()) == [home]


def test_default_run_is_unchanged_when_no_override_is_set(tmp_path):
    """Regression: with KATAGIRI_DATA_HOME unset, behavior matches pre-T011."""
    fake_localappdata = tmp_path / "LocalAppData"
    fake_localappdata.mkdir()
    os.environ.pop("KATAGIRI_DATA_HOME", None)
    os.environ.pop("KATAGIRI_CONFIG", None)
    os.environ["LOCALAPPDATA"] = str(fake_localappdata)
    config_mod.reset_config_cache()

    assert config_mod.config_dir() == fake_localappdata / config_mod.APP_DIR_NAME
    assert config_mod.config_path() == (
        fake_localappdata / config_mod.APP_DIR_NAME / config_mod.CONFIG_FILE_NAME
    )


def test_katagiri_config_takes_precedence_over_katagiri_data_home_for_the_file(tmp_path):
    """KATAGIRI_CONFIG > KATAGIRI_DATA_HOME > default (config.config_dir docstring)."""
    data_home = tmp_path / "instance-b"
    explicit_config = tmp_path / "elsewhere" / "custom-config.toml"
    os.environ["KATAGIRI_DATA_HOME"] = str(data_home)
    os.environ["KATAGIRI_CONFIG"] = str(explicit_config)
    config_mod.reset_config_cache()

    # config_path() honors KATAGIRI_CONFIG verbatim...
    assert config_mod.config_path() == explicit_config
    # ...but config_dir() (and everything derived from it -- logs) still
    # follows KATAGIRI_DATA_HOME: the two knobs are independent.
    assert config_mod.config_dir() == data_home
    assert applog.logs_dir() == data_home / applog.LOGS_DIR_NAME


@pytest.mark.parametrize("bad_value", ["", "relative/path", "..\\also-relative"])
def test_invalid_data_home_raises_and_writes_nothing_to_the_default_home(tmp_path, bad_value):
    fake_localappdata = tmp_path / "LocalAppData"
    fake_localappdata.mkdir()
    os.environ["LOCALAPPDATA"] = str(fake_localappdata)
    os.environ.pop("KATAGIRI_CONFIG", None)
    os.environ["KATAGIRI_DATA_HOME"] = bad_value
    config_mod.reset_config_cache()

    with pytest.raises(config_mod.ConfigError) as excinfo:
        config_mod.config_dir()
    assert "KATAGIRI_DATA_HOME must be an absolute path" in str(excinfo.value)
    assert bad_value in str(excinfo.value) or repr(bad_value) in str(excinfo.value)
    assert "will not silently fall back" in str(excinfo.value)

    # The default home was never touched: no ConfigError recovery path may
    # fall back to it (D-46 -- that would risk a side-by-side test instance
    # writing into the real study database's home).
    assert not (fake_localappdata / config_mod.APP_DIR_NAME).exists()


# ---------------------------------------------------------------------------
# katagiri.installer: --data-home (T012), sandboxed repo_root throughout
# ---------------------------------------------------------------------------


def test_installer_data_home_flag_creates_dir_and_resolves_config_under_it(
    tmp_path, monkeypatch
):
    fake_repo_root = tmp_path / "fake-checkout"
    fake_repo_root.mkdir()
    data_home = tmp_path / "second-instance"
    assert not data_home.exists()

    monkeypatch.setattr(installer, "_repo_root", lambda: fake_repo_root)
    os.environ.pop("KATAGIRI_CONFIG", None)

    exit_code = installer.main(["--data-home", str(data_home), "--check"])

    assert exit_code in (0, 1)  # --check's exit code reflects doctor status, not this flag
    assert data_home.is_dir()
    assert os.environ["KATAGIRI_DATA_HOME"] == str(data_home)
    assert config_mod.config_dir() == data_home
    assert config_mod.config_path() == data_home / config_mod.CONFIG_FILE_NAME

    # Persisted into the *sandboxed* agent/.env, never this worktree's real one.
    env_file = fake_repo_root / "agent" / ".env"
    assert env_file.exists()
    text = env_file.read_text(encoding="utf-8")
    assert f"KATAGIRI_DATA_HOME={data_home.as_posix()}" in text

    real_env_file = REPO_ROOT / "agent" / ".env"
    if real_env_file.exists():
        assert "second-instance" not in real_env_file.read_text(encoding="utf-8")


def test_installer_data_home_persists_env_in_place_on_a_rerun(tmp_path, monkeypatch):
    """A second --data-home run updates the one line, in place -- no duplicate,
    nothing else in agent/.env disturbed (the installer's own doc comment)."""
    fake_repo_root = tmp_path / "fake-checkout"
    (fake_repo_root / "agent").mkdir(parents=True)
    (fake_repo_root / "agent" / ".env").write_text(
        "KATAGIRI_PYTHON=C:/some/python.exe\nKATAGIRI_DATA_HOME=C:/stale/home\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(installer, "_repo_root", lambda: fake_repo_root)

    first_home = tmp_path / "home-1"
    second_home = tmp_path / "home-2"

    installer.main(["--data-home", str(first_home), "--check"])
    installer.main(["--data-home", str(second_home), "--check"])

    text = (fake_repo_root / "agent" / ".env").read_text(encoding="utf-8")
    lines = text.splitlines()
    data_home_lines = [line for line in lines if line.startswith("KATAGIRI_DATA_HOME=")]
    assert data_home_lines == [f"KATAGIRI_DATA_HOME={second_home.as_posix()}"]
    assert "KATAGIRI_PYTHON=C:/some/python.exe" in lines


@pytest.mark.parametrize("bad_value", ["relative\\path", "relative/path", "./here"])
def test_installer_data_home_invalid_value_errors_and_writes_nothing(
    tmp_path, monkeypatch, capsys, bad_value
):
    """A relative ``--data-home`` value is rejected before anything is created
    or persisted (empty string is not exercised here: argparse's ``if
    args.data_home:`` truthiness check treats it as "flag not given" rather
    than reaching ``_resolve_data_home`` at all -- covered instead by
    ``test_installer_without_data_home_flag_leaves_env_and_default_home_untouched``).
    """
    fake_repo_root = tmp_path / "fake-checkout"
    fake_repo_root.mkdir()
    monkeypatch.setattr(installer, "_repo_root", lambda: fake_repo_root)
    os.environ.pop("KATAGIRI_DATA_HOME", None)

    exit_code = installer.main(["--data-home", bad_value, "--check"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "must be an absolute path" in captured.err

    assert "KATAGIRI_DATA_HOME" not in os.environ
    assert not (fake_repo_root / "agent").exists()


def test_installer_without_data_home_flag_leaves_env_and_default_home_untouched(
    tmp_path, monkeypatch
):
    fake_repo_root = tmp_path / "fake-checkout"
    fake_repo_root.mkdir()
    fake_localappdata = tmp_path / "LocalAppData"
    fake_localappdata.mkdir()
    monkeypatch.setattr(installer, "_repo_root", lambda: fake_repo_root)
    os.environ.pop("KATAGIRI_DATA_HOME", None)
    os.environ.pop("KATAGIRI_CONFIG", None)
    os.environ["LOCALAPPDATA"] = str(fake_localappdata)
    config_mod.reset_config_cache()

    exit_code = installer.main(["--check"])

    assert exit_code in (0, 1)
    assert "KATAGIRI_DATA_HOME" not in os.environ
    assert not (fake_repo_root / "agent").exists()
    assert config_mod.config_dir() == fake_localappdata / config_mod.APP_DIR_NAME


# ---------------------------------------------------------------------------
# Two live servers, two data homes, distinguishable connection_status (T002)
# ---------------------------------------------------------------------------


class _StdioClient:
    """The smallest honest MCP client: newline-delimited JSON-RPC over a pipe.

    Mirrored from test_abc_workflow.py's ``_StdioClient`` (itself mirrored from
    test_averify.py) rather than imported, matching this suite's convention
    that each gate file keeps its own copy.
    """

    def __init__(self, extra_env: dict[str, str]) -> None:
        env = dict(os.environ)
        env.update(extra_env)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "katagiri.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(REPO_ROOT),
        )
        self._next_id = 0

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(
            (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        )
        self.process.stdin.flush()

    def _read(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError(
                "the MCP server closed stdout before answering; stderr was:\n"
                + self._drain_stderr()
            )
        return json.loads(line.decode("utf-8"))

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": {} if params is None else params,
            }
        )
        response = self._read()
        assert response["jsonrpc"] == "2.0", response
        assert response["id"] == self._next_id, response
        return response

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def initialize(self, client_name: str) -> None:
        initialized = self.call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": "1"},
            },
        )
        assert "error" not in initialized, initialized
        self.notify("notifications/initialized")

    def _drain_stderr(self) -> str:
        assert self.process.stderr is not None
        return self.process.stderr.read().decode("utf-8", "replace")

    def close(self, timeout: float = 15) -> str:
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged server
            self.process.kill()
            self.process.wait(timeout=timeout)
        stderr = self._drain_stderr()
        assert self.process.stdout is not None
        self.process.stdout.close()
        self.process.stderr.close()
        return stderr


def _tool_payload(response: dict[str, Any]) -> Any:
    assert "error" not in response, response
    result = response["result"]
    assert result.get("isError") is not True, result
    if "structuredContent" in result and result["structuredContent"] is not None:
        return result["structuredContent"]
    blocks = [
        block["text"] for block in result.get("content", []) if block.get("type") == "text"
    ]
    assert blocks, f"no readable content in {result}"
    return json.loads(blocks[0])


def _call(client: _StdioClient, name: str) -> Any:
    response = client.call("tools/call", {"name": name, "arguments": {}})
    return _tool_payload(response)


@pytest.mark.mcp
def test_two_data_homes_run_concurrent_servers_with_distinct_connection_status(tmp_path):
    """FR-016/FR-017: two side-by-side servers never share config/db/logs, and
    ``connection_status`` reports which is which in one call.

    Each subprocess gets its own ``KATAGIRI_DATA_HOME`` (env-sourced override,
    T011) instead of a shared ``LOCALAPPDATA``, so there is no reliance on the
    default-home path at all; ``LOCALAPPDATA`` is still sandboxed to a
    throwaway directory per process as defense in depth, in case anything ever
    falls back to it.
    """
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    home_a.mkdir()
    home_b.mkdir()
    localappdata_a = tmp_path / "localappdata-a"
    localappdata_b = tmp_path / "localappdata-b"
    localappdata_a.mkdir()
    localappdata_b.mkdir()

    client_a = _StdioClient(
        {"KATAGIRI_DATA_HOME": str(home_a), "LOCALAPPDATA": str(localappdata_a)}
    )
    client_b = _StdioClient(
        {"KATAGIRI_DATA_HOME": str(home_b), "LOCALAPPDATA": str(localappdata_b)}
    )
    stderr_a = stderr_b = ""
    try:
        client_a.initialize("kata-instances-a")
        client_b.initialize("kata-instances-b")

        ping_a = _call(client_a, "ping")
        ping_b = _call(client_b, "ping")
        assert ping_a["status"] == "ok"
        assert ping_b["status"] == "ok"

        status_a = _call(client_a, "connection_status")
        status_b = _call(client_b, "connection_status")

        assert status_a["status"] == "ok"
        assert status_b["status"] == "ok"
        assert status_a["data_home_source"] == "env"
        assert status_b["data_home_source"] == "env"
        assert Path(status_a["data_home"]) == home_a
        assert Path(status_b["data_home"]) == home_b
        assert status_a["data_home"] != status_b["data_home"]

        # Everything derived from data_home is distinct too: config, db, log.
        assert status_a["config_path"] != status_b["config_path"]
        assert status_a["db_path"] != status_b["db_path"]
        assert status_a["log_file_path"] != status_b["log_file_path"]
        assert Path(status_a["config_path"]).parent == home_a
        assert Path(status_b["config_path"]).parent == home_b
        assert status_a["pid"] != status_b["pid"]

        # Both processes are still alive and independently answering: neither
        # start-up nor the other's tool call disturbed this one.
        assert _call(client_a, "ping")["status"] == "ok"
        assert _call(client_b, "ping")["status"] == "ok"
    finally:
        stderr_a = client_a.close()
        stderr_b = client_b.close()

    for stderr in (stderr_a, stderr_b):
        assert "starting katagiri" in stderr, stderr[-2000:]
        assert "Traceback (most recent call last)" not in stderr, stderr[-4000:]
