"""Connection configuration for the katagiri-agent.

This module builds the connection dict that :class:`MultiServerMCPClient`
(``langchain_mcp_adapters.client``) consumes, for **two** MCP servers:

- ``katagiri`` — the custom server, **stdio only** (that is the only transport
  it implements; see ``src/katagiri/mcp_server.py`` in the primary checkout).
- ``obsidian`` — the Obsidian Local REST API MCP server, whose transport is
  **swappable** between stdio (a wrapper) and Streamable HTTP (the plugin's
  built-in ``/mcp/`` endpoint, self-signed cert, bearer token) via one env var.
  Which variant the course's grading environment actually exposes is still an
  open question at the time this module was written (see
  ``specs/005-mcp-assignment/research.md``, "Open, deliberately"); swapping the
  answer must change *configuration only*, never graph code, which is exactly
  what this module exists to guarantee.

Secrets convention: katagiri itself keeps machine-specific paths and the
Obsidian API token out of the repo, under ``%LOCALAPPDATA%\\Katagiri\\config.toml``
(see ``src/katagiri/config.py`` in the primary checkout, D-22). This module does
not violate that convention: it reads its *own* secrets (the agent's OpenRouter
key, and — only for the Streamable HTTP path — the agent's own copy of the demo
vault's bearer token) from ``agent/.env`` (gitignored), never from katagiri's
config file and never by asking katagiri to hand a credential over. Katagiri
still holds the *personal* vault's token; the agent never touches it.

No value is ever logged here. Presence is checked; contents are not printed.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from langchain_mcp_adapters.sessions import StdioConnection, StreamableHttpConnection

# Load agent/.env once, on first import. Real values live only in .env
# (gitignored); .env.example ships with every key present and no real values.
load_dotenv()


class ConfigError(RuntimeError):
    """Raised when the agent's configuration is missing or invalid."""


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise ConfigError(
            f"Environment variable '{key}' is not set. Copy agent/.env.example "
            "to agent/.env and fill it in."
        )
    return value


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# katagiri connection (stdio only)
# ---------------------------------------------------------------------------


def katagiri_connection() -> StdioConnection:
    """Build the stdio connection config for the katagiri MCP server.

    ``KATAGIRI_PYTHON`` must point at an interpreter with katagiri installed
    (the primary checkout's venv during development: agent and katagiri are
    separate uv projects with separate venvs, so the agent cannot simply
    ``import katagiri`` — it has to spawn the server as a subprocess, the same
    way any MCP client would).

    ``PYTHONUTF8=1`` is set explicitly in the subprocess env: the katagiri
    process does not inherit the agent's own ``.env`` (a stdio subprocess only
    gets the env dict handed to it here, not python-dotenv's process-wide
    side effects), and Japanese text over a default Windows console codepage
    (cp1252) corrupts silently rather than raising.
    """
    python_exe = _require_env("KATAGIRI_PYTHON")
    module = os.environ.get("KATAGIRI_MODULE", "katagiri.mcp_server")

    env: dict[str, str] = {"PYTHONUTF8": "1"}
    # KATAGIRI_CONFIG (the demo-profile override, additive in src/katagiri/config.py
    # per plan.md's constraints) is passed through only when set, so the default
    # run still targets the operator's normal %LOCALAPPDATA%\Katagiri config.
    demo_config = os.environ.get("KATAGIRI_CONFIG")
    if demo_config:
        env["KATAGIRI_CONFIG"] = demo_config

    return StdioConnection(
        transport="stdio",
        command=python_exe,
        args=["-m", module],
        env=env,
    )


# ---------------------------------------------------------------------------
# Obsidian connection (swappable: stdio wrapper <-> Streamable HTTP)
# ---------------------------------------------------------------------------


def _httpx_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Build the httpx.AsyncClient used for the Streamable HTTP connection.

    The Obsidian Local REST API plugin serves a **self-signed certificate**.
    ``verify=False`` is applied only when ``OBSIDIAN_VERIFY_TLS`` is explicitly
    set to a falsy value — never as a silent default — so the escape hatch is
    visible in ``.env`` rather than hardcoded here.
    """
    verify: bool | str = _env_bool("OBSIDIAN_VERIFY_TLS", True)
    if verify and (ca_bundle := os.environ.get("OBSIDIAN_CA_BUNDLE")):
        verify = ca_bundle
    return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, verify=verify)


def obsidian_connection() -> StdioConnection | StreamableHttpConnection:
    """Build the connection config for the Obsidian Local REST API server.

    Transport is chosen by ``OBSIDIAN_TRANSPORT`` (``streamable_http`` or
    ``stdio``); everything else needed for that transport is read from the
    matching env vars. Graph code (``clients.py``, ``graph.py``) only ever
    calls this function — it never branches on transport itself.
    """
    transport = os.environ.get("OBSIDIAN_TRANSPORT", "streamable_http").strip().lower()

    if transport == "stdio":
        python_exe = _require_env("OBSIDIAN_STDIO_COMMAND")
        args_raw = os.environ.get("OBSIDIAN_STDIO_ARGS", "")
        args = args_raw.split() if args_raw else []
        return StdioConnection(
            transport="stdio",
            command=python_exe,
            args=args,
            env={"PYTHONUTF8": "1"},
        )

    if transport == "streamable_http":
        url = _require_env("OBSIDIAN_MCP_URL")
        token = _require_env("OBSIDIAN_API_TOKEN")
        return StreamableHttpConnection(
            transport="streamable_http",
            url=url,
            headers={"Authorization": f"Bearer {token}"},
            httpx_client_factory=_httpx_client_factory,
        )

    raise ConfigError(
        f"Unknown OBSIDIAN_TRANSPORT '{transport}'. Expected 'stdio' or "
        "'streamable_http'."
    )


def mcp_connections() -> dict[str, StdioConnection | StreamableHttpConnection]:
    """The full connection dict handed to ``MultiServerMCPClient``."""
    return {
        "katagiri": katagiri_connection(),
        "obsidian": obsidian_connection(),
    }


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """Non-connection settings the graph and model layer need."""

    openrouter_api_key: str
    openrouter_model: str

    @classmethod
    def load(cls) -> "AgentSettings":
        return cls(
            openrouter_api_key=_require_env("OPENROUTER_API_KEY"),
            openrouter_model=_require_env("OPENROUTER_MODEL"),
        )
