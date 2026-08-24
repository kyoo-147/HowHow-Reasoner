from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from scripts import harth_v2_run as runner


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True)


def _identity_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "identity-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "src/howhow/episodes/harth/v2").mkdir(parents=True)
    (repo / "scripts/harth_v2_run.py").write_bytes(b"#!/usr/bin/env python3\nvalue = 1\n")
    (repo / "src/howhow/episodes/harth/v2/engine.py").write_bytes(b"value = 2\n")
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", "identity fixture")
    return repo


def test_code_hash_is_stable_across_checkout_line_endings(tmp_path, monkeypatch) -> None:
    repo = _identity_repo(tmp_path)
    monkeypatch.setattr(runner, "ROOT", repo)
    lf_hash = runner.code_hash()
    (repo / "scripts/harth_v2_run.py").write_bytes(b"#!/usr/bin/env python3\r\nvalue = 1\r\n")
    crlf_hash = runner.code_hash()
    assert crlf_hash == lf_hash


def test_code_hash_is_stable_across_same_commit_worktrees(tmp_path, monkeypatch) -> None:
    repo = _identity_repo(tmp_path)
    other = tmp_path / "other-worktree"
    _git(repo, "worktree", "add", "--quiet", str(other), "HEAD")
    monkeypatch.setattr(runner, "ROOT", repo)
    first = runner.code_hash()
    monkeypatch.setattr(runner, "ROOT", other)
    assert runner.code_hash() == first


def test_dirty_modified_and_untracked_source_are_rejected(tmp_path, monkeypatch) -> None:
    repo = _identity_repo(tmp_path)
    monkeypatch.setattr(runner, "ROOT", repo)
    (repo / "scripts/harth_v2_run.py").write_bytes(b"modified\n")
    assert runner.git("status", "--porcelain")
    _git(repo, "checkout", "--", ".")
    (repo / "src/howhow/episodes/harth/v2/new.py").write_bytes(b"untracked\n")
    assert runner.git("status", "--porcelain")
    (repo / "src/howhow/episodes/harth/v2/engine.py").unlink()
    assert runner.git("status", "--porcelain")


def inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    protocol = tmp_path / "protocol.json"
    protocol.write_text(
        json.dumps(
            {
                "protocol_id": "harth-calibration-v2",
                "budget": {
                    "max_outer_folds": 22,
                    "sensor_configurations": 3,
                    "calibration_states": 2,
                    "wall_clock_minutes": 30,
                },
            }
        ),
        encoding="utf-8",
    )
    archive = tmp_path / "harth.zip"
    archive.write_bytes(b"deterministic archive fixture")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "protocol_id": "harth-calibration-v2",
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "seed": 0,
                "fold_strategy": "nested_subject_held_out_loso",
                "sensor_configurations": 3,
                "calibration_states": 2,
                "bootstrap_reps": 2000,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    return protocol, config, archive, output


def args_for(tmp_path: Path, **overrides: object):
    protocol, config, archive, output = inputs(tmp_path)
    values = {
        "protocol": protocol,
        "config": config,
        "archive": archive,
        "output": output,
        "checkpoint": output / "checkpoint.json",
        "seed": 0,
        "bootstrap_reps": 2000,
        "max_outer_folds": 22,
        "wall_clock_minutes": 30,
        "preflight_only": True,
        "execute_real": False,
    }
    values.update(overrides)
    return type("Args", (), values)()


def test_preflight_writes_manifest_without_metrics(tmp_path, monkeypatch) -> None:
    args = args_for(tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "git", lambda *items: "abc123" if items[-1] == "HEAD" else "")
    path = runner.preflight(args)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS"
    assert manifest["scientific_metrics"] is False
    assert manifest["seed"] == 0
    assert not (args.output / "checkpoint.json").exists()


def test_dirty_tree_and_unknown_archive_checksum_fail_closed(tmp_path, monkeypatch) -> None:
    args = args_for(tmp_path)
    monkeypatch.setattr(runner, "git", lambda *items: " M source.py")
    try:
        runner.preflight(args)
    except runner.PreflightFailure as exc:
        assert "dirty" in str(exc)
    else:
        raise AssertionError("dirty tree was accepted")

    args = args_for(tmp_path / "unknown")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config["archive_sha256"] = "REQUIRED_OPERATOR_CHECKSUM"
    args.config.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(runner, "git", lambda *items: "")
    try:
        runner.preflight(args)
    except runner.PreflightFailure as exc:
        assert "unknown checksum" in str(exc)
    else:
        raise AssertionError("unknown archive checksum was accepted")


def test_nonempty_output_and_real_consent_are_not_bypassed(tmp_path, monkeypatch) -> None:
    args = args_for(tmp_path)
    args.output.mkdir()
    (args.output / "old.json").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(runner, "git", lambda *items: "")
    try:
        runner.preflight(args)
    except runner.PreflightFailure as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("nonempty output was accepted")

    args = args_for(tmp_path / "consent")
    monkeypatch.setattr(runner, "git", lambda *items: "")
    monkeypatch.delenv(runner.REAL_CONSENT_ENV, raising=False)
    monkeypatch.setattr(runner.sys, "argv", ["harth_v2_run.py", "--execute-real"])
    args.execute_real = True
    monkeypatch.setattr(
        runner, "parser", lambda: type("Parser", (), {"parse_args": lambda self: args})()
    )
    assert runner.main() == 1
    assert not list(args.output.glob("*.metrics.json"))
