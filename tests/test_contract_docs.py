"""Drift check for docs/assignment/tool-contracts.md (T018, FR-012/FR-013).

``scripts/gen_tool_contracts.py`` is a script, not a package, so it is loaded
here via ``importlib`` from its file path rather than imported normally. The
main test regenerates the doc in memory from the live
``katagiri.tool_registry`` and compares it against the committed file — the
same thing ``gen_tool_contracts.py --check`` does, in-process, so a CI-less
repo still catches drift in ``uv run pytest``.

No DB, no fixtures, no MCP server: this module needs nothing beyond the
registry module and the committed markdown file, so it stays in the fast
general test group (see tests/conftest.py) rather than ``mcp`` or ``compile``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from katagiri.tool_registry import TOOL_SPECS, tool_names

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "gen_tool_contracts.py"
DOC_PATH = REPO_ROOT / "docs" / "assignment" / "tool-contracts.md"


def _load_gen_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_tool_contracts", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    return _load_gen_module()


def test_committed_doc_matches_registry(gen: ModuleType) -> None:
    """The committed file must equal what the generator would produce now.

    A mismatch means tool_registry.py changed and nobody re-ran the
    generator — this is the drift check FR-013 requires, wired into pytest.
    """
    assert DOC_PATH.is_file(), (
        f"{DOC_PATH} is missing. Run scripts/gen_tool_contracts.py."
    )
    existing = DOC_PATH.read_text(encoding="utf-8")
    rendered = gen.render(existing)

    if rendered != existing:
        drifted = gen._drifted_tools(existing, rendered) or ["<unknown>"]
        pytest.fail(
            "docs/assignment/tool-contracts.md is out of date with "
            "tool_registry.py (drifted: " + ", ".join(drifted) + "). "
            "Run scripts/gen_tool_contracts.py to regenerate it."
        )


def test_every_registered_tool_has_a_section() -> None:
    """All 26 tools get exactly one GENERATED block and one HAND block."""
    existing = DOC_PATH.read_text(encoding="utf-8")
    names = tool_names()
    assert len(names) == len(TOOL_SPECS)

    for name in names:
        assert existing.count(f"<!-- BEGIN GENERATED: {name} -->") == 1
        assert existing.count(f"<!-- END GENERATED: {name} -->") == 1
        assert existing.count(f"<!-- BEGIN HAND: {name} -->") == 1
        assert existing.count(f"<!-- END HAND: {name} -->") == 1


def test_render_preserves_hand_blocks_byte_for_byte(gen: ModuleType) -> None:
    """Editing a HAND block, then regenerating, must not clobber the edit.

    This is the T020 contract: hand-written prose survives regeneration
    because the generator reads the existing file and copies HAND blocks
    forward unchanged rather than rebuilding them.
    """
    original = DOC_PATH.read_text(encoding="utf-8")
    hand_written = "- **Purpose**: Checks the server is alive and reports versions."
    perturbed = original.replace(
        "<!-- BEGIN HAND: ping -->\n- **Purpose**: _TODO (T020)_",
        f"<!-- BEGIN HAND: ping -->\n{hand_written}",
        1,
    )
    assert perturbed != original, "fixture assumption broke: replacement did not match"

    rerendered = gen.render(perturbed)

    assert hand_written in rerendered
    # Every other tool's still-placeholder HAND block is untouched.
    for other in ("known_word", "known_set_stats", "lookup"):
        assert (
            f"<!-- BEGIN HAND: {other} -->\n- **Purpose**: _TODO (T020)_"
            in rerendered
        )


def test_check_mode_detects_induced_drift(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Perturbing a GENERATED block in a throwaway copy must fail --check.

    Operates on a scratch copy under tmp_path; the committed file is only
    ever read here, never written.
    """
    real_text = DOC_PATH.read_text(encoding="utf-8")
    perturbed = real_text.replace(
        "- **Model-facing description**: Liveness check: server status and versions.",
        "- **Model-facing description**: Something the registry never said.",
        1,
    )
    assert perturbed != real_text, "fixture assumption broke: replacement did not match"

    scratch = tmp_path / "tool-contracts.md"
    scratch.write_text(perturbed, encoding="utf-8")
    monkeypatch.setattr(gen, "DOC_PATH", scratch)

    exit_code = gen.main(["--check"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "ping" in output
    assert "run scripts/gen_tool_contracts.py" in output.lower()
    # --check must never write, even on drift.
    assert scratch.read_text(encoding="utf-8") == perturbed


def test_check_mode_passes_on_untouched_copy(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity check: an unperturbed copy reports clean, exit 0."""
    scratch = tmp_path / "tool-contracts.md"
    scratch.write_text(DOC_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(gen, "DOC_PATH", scratch)

    assert gen.main(["--check"]) == 0
