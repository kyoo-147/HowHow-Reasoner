from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
CHECKS = (ROOT / "scripts/check_all.py").read_text(encoding="utf-8")
DOCS = (ROOT / "docs/DEVELOPMENT.md").read_text(encoding="utf-8")


def test_workflow_has_two_platforms_and_locked_gates() -> None:
    assert "os: [ubuntu-latest, windows-latest]" in WORKFLOW
    assert 'python-version: "3.12"' in WORKFLOW
    assert "uv sync --locked" in WORKFLOW
    assert "uv run python scripts/check_all.py" in WORKFLOW
    assert "pnpm/action-setup@v4" in WORKFLOW
    assert "version: 10.33.2" in WORKFLOW
    assert "cache-dependency-path: pnpm-lock.yaml" in WORKFLOW


def test_workflow_is_private_minimal_and_bounded() -> None:
    assert "contents: read" in WORKFLOW
    assert "cancel-in-progress: true" in WORKFLOW
    assert "timeout-minutes: 20" in WORKFLOW
    assert "upload-artifact" not in WORKFLOW
    assert "secrets." not in WORKFLOW
    assert "--real-miktex" not in WORKFLOW


def test_local_gate_avoids_private_paths_and_labels_optional_miktex() -> None:
    assert '"git", "ls-files", "--cached", "*.py"' in CHECKS
    assert "--exclude-standard" not in CHECKS
    assert '"--real-miktex"' in CHECKS
    assert '"not real_miktex"' in CHECKS
    assert "UNVERIFIED" in DOCS
    assert "BLOCKED" in DOCS
