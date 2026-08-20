"""Isolation guard for the demo profile (T011, US3).

The demo profile (T007's ``KATAGIRI_CONFIG`` override, T009's fixture vault
and config template, T008's fixture DB builder) must never let a demo run
touch personal data or the personal Obsidian credential. This module is the
mechanical check of that promise, on two independent paths:

- **katagiri's own config loading** (``src/katagiri/config.py``): with a demo
  profile active, ``load_config()`` must resolve the fixture DB and fixture
  vault -- never a personal path -- and ``obsidian_api_token`` (one of
  ``_SECRET_KEYS``, excluded from ``Config.__repr__``) must stay unset no
  matter what token-shaped environment variables are sitting in the process
  environment, because katagiri never reads that key from the environment at
  all (only from ``config.toml``, see ``load_config``).
- **the agent's own env-loading path** (``agent/src/katagiri_agent/config.py``,
  which loads ``agent/.env`` via python-dotenv): it must never fall back to
  the personal ``%LOCALAPPDATA%\\Katagiri`` config. That module is read
  read-only, via ``ast``, rather than imported -- the agent is a separate uv
  project with its own venv (langchain_mcp_adapters, httpx, python-dotenv),
  and this check must never require installing it.

The two check functions below (``check_demo_config_isolation`` and
``check_agent_config_no_personal_fallback``) are written to be imported and
called directly -- not only run under pytest -- because T026's pre-flight
script calls them before every demo run. Both raise ``DemoIsolationError``
naming the offending key, path, or line -- never a secret value.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from katagiri import config as config_mod

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENT_CONFIG_PATH = _REPO_ROOT / "agent" / "src" / "katagiri_agent" / "config.py"

# Environment variable names a personal Obsidian token might plausibly travel
# under, named explicitly so a regression fails on the same key it would leak
# in production rather than a made-up one. katagiri's own config.py never
# reads any of these (obsidian_api_token comes only from config.toml); this
# list exists to catch a *future* change that adds such a fallback.
_TOKEN_ENV_CANDIDATES: tuple[str, ...] = (
    "OBSIDIAN_API_TOKEN",
    "KATAGIRI_OBSIDIAN_API_TOKEN",
)


class DemoIsolationError(AssertionError):
    """Raised when the demo profile is not isolated from personal data.

    Message text is built only from key names, module paths, and line
    numbers -- never a secret value -- so it is safe to let this propagate
    into a pytest failure, a log line, or T026's pre-flight console output.
    """


@dataclass(frozen=True)
class DemoProfileFixture:
    """A self-contained demo profile written under a test's tmp_path."""

    config_path: Path
    db_path: Path
    vault_path: Path


def build_demo_profile(tmp_path: Path) -> DemoProfileFixture:
    """Write a demo-profile config.toml under ``tmp_path`` and return it.

    Mirrors ``tests/demo_fixtures/demo-config.toml.example``'s key set
    (vault_path, db_path; obsidian_api_token left unset, exactly as that
    template prescribes for the demo profile, T009) but with throwaway paths
    under ``tmp_path`` instead of the checked-in fixture tree, so the test
    never depends on ``scripts/build_demo_db.py`` having been run or on
    ``%LOCALAPPDATA%\\Katagiri-demo`` existing on the machine running it.
    """
    db_path = tmp_path / "demo_fixtures" / "demo.db"
    vault_path = tmp_path / "demo_fixtures" / "vault"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"")  # stand-in for the built fixture DB; unused as SQLite here
    vault_path.mkdir(parents=True, exist_ok=True)

    config_path = tmp_path / "demo-config.toml"
    config_path.write_text(
        f'vault_path = "{vault_path.as_posix()}"\n'
        f'db_path = "{db_path.as_posix()}"\n'
        "# obsidian_api_token intentionally left unset for the demo profile\n",
        encoding="utf-8",
    )
    return DemoProfileFixture(config_path=config_path, db_path=db_path, vault_path=vault_path)


def check_demo_config_isolation(
    config: config_mod.Config,
    *,
    expected_db_path: Path,
    expected_vault_path: Path,
) -> None:
    """Assert a loaded demo-profile ``Config`` is isolated from personal data.

    Reusable by T026's pre-flight script: load the ``Config`` with
    ``KATAGIRI_CONFIG`` already pointed at the demo profile, then pass it
    here along with the fixture paths it must resolve to. Raises
    ``DemoIsolationError`` -- naming the offending key, never a value -- if:

    - ``db_path`` does not resolve to the expected fixture DB;
    - ``vault_path`` does not resolve to the expected demo vault;
    - ``obsidian_api_token`` is set at all (the demo profile must never carry
      a usable token -- see ``demo-config.toml.example``'s note on why).
    """
    if config.db_path != expected_db_path:
        raise DemoIsolationError(
            "Demo profile isolation check failed: 'db_path' resolved to "
            f"{config.db_path}, expected the fixture DB at {expected_db_path}. "
            "The demo profile must never resolve to a personal db_path."
        )
    if config.vault_path != expected_vault_path:
        raise DemoIsolationError(
            "Demo profile isolation check failed: 'vault_path' resolved to "
            f"{config.vault_path}, expected the demo vault at {expected_vault_path}. "
            "The demo profile must never resolve to a personal vault_path."
        )
    if config.obsidian_api_token:
        raise DemoIsolationError(
            "Demo profile isolation check failed: 'obsidian_api_token' is set "
            "in the demo profile. It must stay unset -- the personal Obsidian "
            "token must never be resolvable in a demo run. (Value withheld: "
            "this key holds a credential.)"
        )


def _string_call_args(tree: ast.AST):
    """Yield (string value, line number) for every literal string argument
    passed to any function call in the module -- but not docstrings or plain
    comments, which are never call arguments."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for arg in (*node.args, *(kw.value for kw in node.keywords)):
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    yield arg.value, node.lineno


def check_agent_config_no_personal_fallback(
    agent_config_path: Path = _AGENT_CONFIG_PATH,
) -> None:
    """Statically verify the agent's env-loading module has no personal-config
    fallback.

    Reusable by T026's pre-flight script. Read-only: parses the module's
    source with ``ast`` rather than importing it, so this never needs the
    agent's own venv/dependencies (langchain_mcp_adapters, httpx,
    python-dotenv) installed. Two invariants are checked:

    - the module is never passed the literal string ``'LOCALAPPDATA'`` as a
      call argument anywhere (covers ``os.environ.get("LOCALAPPDATA")``,
      ``os.getenv("LOCALAPPDATA")``, or a local helper called the same way)
      -- that is the personal config directory's env var, see
      ``src/katagiri/config.py``'s ``local_app_data``;
    - the module never imports ``katagiri`` (or any ``katagiri.*``
      submodule), which is the only in-repo path that could reach the
      personal config.

    Raises ``DemoIsolationError`` naming the module path and offending line
    number(s) -- there is no secret value to withhold here, only source
    structure.
    """
    if not agent_config_path.is_file():
        raise DemoIsolationError(
            f"Agent config module not found at {agent_config_path}; cannot "
            "verify it has no personal-config fallback."
        )
    source = agent_config_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(agent_config_path))

    offending_import_lines = [
        node.lineno
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Import)
            and any(a.name == "katagiri" or a.name.startswith("katagiri.") for a in node.names)
        )
        or (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "katagiri" or node.module.startswith("katagiri."))
        )
    ]
    if offending_import_lines:
        raise DemoIsolationError(
            f"{agent_config_path} imports the katagiri package directly at "
            f"line(s) {offending_import_lines}; the agent must reach katagiri "
            "only over its stdio MCP connection, never by importing "
            "katagiri.config and falling back to the personal config."
        )

    offending_env_lines = [
        lineno for value, lineno in _string_call_args(tree) if value == "LOCALAPPDATA"
    ]
    if offending_env_lines:
        raise DemoIsolationError(
            f"{agent_config_path} reads the 'LOCALAPPDATA' environment "
            f"variable at line(s) {offending_env_lines}; the agent must never "
            "resolve the personal %LOCALAPPDATA%\\Katagiri config -- its own "
            "secrets belong in agent/.env only."
        )


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Reset the process-wide config cache before and after every test."""
    config_mod.reset_config_cache()
    yield
    config_mod.reset_config_cache()


def test_demo_profile_resolves_fixture_paths_not_personal(tmp_path, monkeypatch):
    profile = build_demo_profile(tmp_path)
    monkeypatch.setenv("KATAGIRI_CONFIG", str(profile.config_path))
    # A personal LOCALAPPDATA is very likely present in the real environment
    # running this test; point it somewhere else so the assertions below
    # would obviously fail if config_path() ever fell back to it once
    # KATAGIRI_CONFIG is set (it must not -- T007's override is unconditional).
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "not-the-personal-one"))
    config_mod.reset_config_cache()

    cfg = config_mod.load_config(create_missing=False)

    check_demo_config_isolation(
        cfg, expected_db_path=profile.db_path, expected_vault_path=profile.vault_path
    )


def test_demo_profile_token_not_leaked_via_environment(tmp_path, monkeypatch):
    profile = build_demo_profile(tmp_path)
    monkeypatch.setenv("KATAGIRI_CONFIG", str(profile.config_path))
    # Simulate a personal token sitting in the process environment under
    # every name katagiri might plausibly read it from; none may reach the
    # demo Config, because katagiri only ever reads this key from config.toml.
    sentinel = "PERSONAL-TOKEN-MUST-NOT-LEAK-4f9c"
    for key in _TOKEN_ENV_CANDIDATES:
        monkeypatch.setenv(key, sentinel)
    config_mod.reset_config_cache()

    cfg = config_mod.load_config(create_missing=False)

    check_demo_config_isolation(
        cfg, expected_db_path=profile.db_path, expected_vault_path=profile.vault_path
    )
    assert cfg.obsidian_api_token is None
    assert sentinel not in repr(cfg)
    assert sentinel not in str(cfg)


def test_demo_profile_with_token_set_fails_loudly_naming_key(tmp_path, monkeypatch):
    """Sanity-check the guard itself: a demo config that DOES carry a token
    must fail, and the failure must name the key without the value."""
    profile = build_demo_profile(tmp_path)
    profile.config_path.write_text(
        profile.config_path.read_text(encoding="utf-8")
        + '\nobsidian_api_token = "leaked-personal-token-value"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KATAGIRI_CONFIG", str(profile.config_path))
    config_mod.reset_config_cache()
    cfg = config_mod.load_config(create_missing=False)

    with pytest.raises(DemoIsolationError) as excinfo:
        check_demo_config_isolation(
            cfg, expected_db_path=profile.db_path, expected_vault_path=profile.vault_path
        )
    message = str(excinfo.value)
    assert "obsidian_api_token" in message
    assert "leaked-personal-token-value" not in message


def test_agent_config_module_has_no_personal_fallback():
    check_agent_config_no_personal_fallback()


def test_agent_config_guard_fails_loudly_on_localappdata_reference(tmp_path):
    """Sanity-check the agent-side guard: a module that DOES read
    LOCALAPPDATA must fail, naming the module path and line -- there is no
    value to withhold for this check, only source structure."""
    decoy = tmp_path / "decoy_config.py"
    decoy.write_text('import os\n\nos.environ.get("LOCALAPPDATA")\n', encoding="utf-8")

    with pytest.raises(DemoIsolationError) as excinfo:
        check_agent_config_no_personal_fallback(decoy)
    message = str(excinfo.value)
    assert "LOCALAPPDATA" in message
    assert str(decoy) in message


def test_agent_config_guard_fails_loudly_on_katagiri_import(tmp_path):
    """Sanity-check the guard's other invariant: importing katagiri directly
    (the only in-repo path to the personal config) must also fail loudly."""
    decoy = tmp_path / "decoy_config.py"
    decoy.write_text("from katagiri import config\n", encoding="utf-8")

    with pytest.raises(DemoIsolationError) as excinfo:
        check_agent_config_no_personal_fallback(decoy)
    message = str(excinfo.value)
    assert "katagiri" in message
    assert str(decoy) in message
