"""Credential auto-discovery from the vault's own plugin store.

When ``config.toml`` names a ``vault_path`` but no ``obsidian_api_token``, the
proxy reads ``<vault>/.obsidian/plugins/obsidian-local-rest-api/data.json`` —
the plugin's own store — and takes the ``apiKey`` from there, so the learner
never has to copy a token by hand. Two rules bound the feature:

*Explicit config wins.* A token or CA bundle set in ``config.toml`` overrides
anything in the plugin store; the store is a fallback, never an authority.

*Failure is an answer, never a downgrade.* A missing, unreadable or malformed
``data.json`` becomes the same ``obsidian_unconfigured`` answer as an unset
token — no traceback, no echoed path or value — and a garbage ``crypto.cert``
falls back to the plain default TLS context with verification still required.
"""

from __future__ import annotations

import io
import json
import ssl
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from katagiri import config as config_mod
from katagiri import obsidian_proxy

PLUGIN_DATA_RELPATH = ".obsidian/plugins/obsidian-local-rest-api/data.json"

# A self-signed certificate with CN=katagiri-test, valid until 2126.
VALID_PEM = """-----BEGIN CERTIFICATE-----
MIIDEzCCAfugAwIBAgIUAqqYS/DiwwwQW7h/tEzEtI4mD4swDQYJKoZIhvcNAQEL
BQAwGDEWMBQGA1UEAwwNa2F0YWdpcmktdGVzdDAgFw0yNjA4MjMxMzI2MTNaGA8y
MTI2MDczMDEzMjYxM1owGDEWMBQGA1UEAwwNa2F0YWdpcmktdGVzdDCCASIwDQYJ
KoZIhvcNAQEBBQADggEPADCCAQoCggEBAJve2+v2v43aWdgtVan9+sLWtSi0Jh8a
yXm8WN1DKaV7GGC4N1ROrj9ve2fA+VTWuizI5VJAbtDSY6bvAy3G5U1rQBJ27dyf
r4E4w50O2glIXSDz0vL3+sK+7IUhjUPB6kpDTzhb7elwhnlDCB0rpIch70rROoPn
XuYq9cMcloo4MpnvADl28EXpatiEUq+1nsk7WJcplCm1JwEDuZ4txBxAV8YbsjTn
AUs7PUHaiHKgBTP3DV4TrzYvSjBJHq1/pUtvumSFeYXp1qoz07x8y5Oj6dgz7Tp5
PCv8SuuZLe/gqZzjlFqkoeePXs7T3ifQko12j7LJ5HpVyHio+do5+/sCAwEAAaNT
MFEwHQYDVR0OBBYEFKotHn96LNDLVtLJEu7MTON+GjaAMB8GA1UdIwQYMBaAFKot
Hn96LNDLVtLJEu7MTON+GjaAMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQEL
BQADggEBACkPNGCMZR4XwjntZ2Cl3Ovwzy8c+EH/Broex5NXNyV2OL9VdjefjXs9
NiT35YuOgeSTHcL3XDkBprPaHjE6LMwY77e90qbz3IVtd+O0rIhE2bt9Kbv17ajQ
4GVM/ajntynVNXvF3cxtvXs6sesLao8U65rCtNTfah1xMoq9ILNYN1qaIS8LNHAi
eP9o89fGRrtRwEFClBrN+EtbaiVUG2fvdoBI+DahQi71M2OhVS1dEnMtqt5wLZdE
2PiOsyxiwnJHEK3IZzSbhLxrbTiLTkWSuNT4RtfqV7EmCebH6iltUZBsiT5fumDI
kKsaM6iSxmLSKB1OfsfQJH+ymcvN3kY=
-----END CERTIFICATE-----
"""


# ---------------------------------------------------------------------------
# Helpers: configuration, plugin store, and a fake HTTP seam
# ---------------------------------------------------------------------------


def _write_config(root: Path, body: str) -> Path:
    cfg_dir = root / "Katagiri"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _write_plugin_data(vault: Path, payload: dict) -> Path:
    """Create ``<vault>/.obsidian/plugins/obsidian-local-rest-api/data.json``."""
    path = vault / PLUGIN_DATA_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """%LOCALAPPDATA% pointed at a scratch dir; each test writes its own config."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


@pytest.fixture
def vault(scratch):
    """A vault directory the config's ``vault_path`` can point at."""
    path = scratch / "Vault"
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeResponse:
    """Minimal stand-in for ``http.client.HTTPResponse``."""

    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        content_type: str | None = "text/markdown",
    ) -> None:
        self.status = status
        self.headers = {} if content_type is None else {"Content-Type": content_type}
        self._buffer = io.BytesIO(body)
        self.closed = False

    def read(self, amount: int | None = None) -> bytes:
        return self._buffer.read() if amount is None else self._buffer.read(amount)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FakeHttp:
    """Records every request and hands back a canned response or exception."""

    def __init__(self) -> None:
        self.requests: list[urllib.request.Request] = []
        self.result: Any = FakeResponse(b"")

    def __call__(self, request: urllib.request.Request) -> Any:
        self.requests.append(request)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    @property
    def last(self) -> urllib.request.Request:
        assert self.requests, "no HTTP request was made"
        return self.requests[-1]


@pytest.fixture
def http(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(obsidian_proxy, "_open_url", fake)
    return fake


# ---------------------------------------------------------------------------
# The relpath is a named module constant
# ---------------------------------------------------------------------------


def test_the_plugin_store_path_is_a_module_constant():
    assert obsidian_proxy.PLUGIN_DATA_RELPATH == PLUGIN_DATA_RELPATH


# ---------------------------------------------------------------------------
# Token discovery
# ---------------------------------------------------------------------------


def test_token_is_auto_discovered_from_the_plugin_store(scratch, vault, http):
    _write_config(scratch, f'vault_path = "{vault.as_posix()}"\n')
    _write_plugin_data(vault, {"apiKey": "auto-key-123", "crypto": {}})
    config_mod.reset_config_cache()
    http.result = FakeResponse(b"# note")

    answer = obsidian_proxy.read_vault_file("a.md")

    assert answer["ok"] is True
    assert http.last.get_header("Authorization") == "Bearer auto-key-123"


def test_a_configured_token_overrides_the_plugin_store(scratch, vault, http):
    _write_config(
        scratch,
        f'vault_path = "{vault.as_posix()}"\n'
        'obsidian_api_token = "explicit-key"\n',
    )
    _write_plugin_data(vault, {"apiKey": "auto-key-123", "crypto": {}})
    config_mod.reset_config_cache()
    http.result = FakeResponse(b"# note")

    answer = obsidian_proxy.read_vault_file("a.md")

    assert answer["ok"] is True
    assert http.last.get_header("Authorization") == "Bearer explicit-key"


# ---------------------------------------------------------------------------
# Discovery failure is an answer, not a traceback
# ---------------------------------------------------------------------------


def test_no_vault_path_and_no_token_is_unconfigured(scratch, http):
    _write_config(scratch, "")
    config_mod.reset_config_cache()

    answer = obsidian_proxy.read_vault_file("note.md")

    assert answer["ok"] is False
    assert answer["error"] == "obsidian_unconfigured"
    assert http.requests == [], "nothing may be sent without a token"


def test_missing_plugin_data_file_is_unconfigured(scratch, vault, http):
    _write_config(scratch, f'vault_path = "{vault.as_posix()}"\n')
    config_mod.reset_config_cache()

    for answer in (
        obsidian_proxy.read_vault_file("note.md"),
        obsidian_proxy.list_vault_dir(None),
    ):
        assert answer["ok"] is False
        assert answer["error"] == "obsidian_unconfigured"
    assert http.requests == [], "a failed discovery must never reach the network"


@pytest.mark.parametrize(
    "raw",
    [
        b"{not json at all",
        json.dumps({"apiKey": ""}).encode(),
        json.dumps({"apiKey": 42}).encode(),
        json.dumps({}).encode(),
    ],
    ids=["invalid_json", "blank_key", "non_string_key", "no_key"],
)
def test_malformed_plugin_data_is_an_answer_not_a_traceback(scratch, vault, http, raw):
    _write_config(scratch, f'vault_path = "{vault.as_posix()}"\n')
    data = vault / PLUGIN_DATA_RELPATH
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_bytes(raw)
    config_mod.reset_config_cache()

    answer = obsidian_proxy.read_vault_file("note.md")

    assert answer["ok"] is False
    assert answer["error"] == "obsidian_unconfigured"
    assert http.requests == [], "a malformed store must never reach the network"


# ---------------------------------------------------------------------------
# The plugin certificate and the TLS context
# ---------------------------------------------------------------------------


def test_plugin_cert_is_loaded_into_the_tls_context(scratch, vault):
    _write_config(scratch, f'vault_path = "{vault.as_posix()}"\n')
    _write_plugin_data(vault, {"apiKey": "k", "crypto": {"cert": VALID_PEM}})
    config_mod.reset_config_cache()

    ctx = obsidian_proxy._tls_context()

    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    subjects = [cert.get("subject", ()) for cert in ctx.get_ca_certs()]
    assert any(
        ("commonName", "katagiri-test") in rdn
        for subject in subjects
        for rdn in subject
    ), "the plugin's own CA must be in the trust store"


def test_a_garbage_plugin_cert_never_disables_verification(scratch, vault):
    _write_config(scratch, f'vault_path = "{vault.as_posix()}"\n')
    _write_plugin_data(vault, {"apiKey": "k", "crypto": {"cert": "not a pem"}})
    config_mod.reset_config_cache()

    ctx = obsidian_proxy._tls_context()

    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_a_configured_ca_bundle_still_wins(scratch, vault):
    missing = scratch / "missing.pem"
    _write_config(
        scratch,
        f'vault_path = "{vault.as_posix()}"\n'
        f'obsidian_ca_bundle = "{missing.as_posix()}"\n',
    )
    _write_plugin_data(vault, {"apiKey": "k", "crypto": {"cert": VALID_PEM}})
    config_mod.reset_config_cache()

    with pytest.raises(obsidian_proxy.ObsidianTlsConfiguration):
        obsidian_proxy._tls_context()
