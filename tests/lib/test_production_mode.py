"""Production mode on a synthetic VidQwik root: every layer is exercised for real.

A temporary root holds a small OpenMontage checkout (a real git repository),
channel policy and a project. Nothing here touches the real installation.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from lib import production_mode as pm
from lib.checkpoint import read_checkpoint, write_checkpoint


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "VidQwik AI"
    om = root / "OpenMontage"
    files = {"lib/engine.py": "X = 1\n", "tools/tool.py": "T = 1\n",
             "skills/pipelines/relaxation/edit-director.md": "# edit\n",
             "pipeline_defs/relaxation.yaml": "name: relaxation\n",
             "schemas/a.json": "{}\n", "config.yaml": "a: 1\n",
             "lib/__pycache__/engine.cpython-312.pyc": "bytecode"}
    for rel, text in files.items():
        (om / rel).parent.mkdir(parents=True, exist_ok=True)
        (om / rel).write_text(text)
    (root / "Channels" / "channel_9999").mkdir(parents=True)
    (root / "Channels" / "channel_9999" / "BRAND.md").write_text("# brand\n")
    (root / "ARCHITECTURE.md").write_text("# arch\n")
    project = om / "projects" / "channel_9999__video_0001"
    project.mkdir(parents=True)
    (project / "project.json").write_text(json.dumps(
        {"version": "1.0", "project_id": project.name, "pipeline_type": "relaxation",
         "title": "fixture"}))
    (om / ".gitignore").write_text("projects/\n__pycache__/\n.claude/settings.local.json\n")
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["add", "."], ["commit", "-qm", "engine"]):
        assert _git(om, *args).returncode == 0
    yield om
    for path in root.rglob("*"):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


def test_enable_freezes_the_engine_and_policy_but_not_projects(root):
    info = pm.enable(root, operator="operator")
    assert info["active"] and info["protected_files"] > 5
    for path in (root / "lib" / "engine.py",
                 root / "skills" / "pipelines" / "relaxation" / "edit-director.md",
                 root.parent / "Channels" / "channel_9999" / "BRAND.md",
                 root.parent / "ARCHITECTURE.md", root.parent / ".claude" / "settings.json"):
        with pytest.raises(PermissionError):
            path.write_text("patched during production")
    project_file = root / "projects" / "channel_9999__video_0001" / "work.txt"
    project_file.write_text("production writes artifacts and media")      # still allowed
    (root / "lib" / "__pycache__" / "new.pyc").write_bytes(b"x")           # bytecode still ok
    state = pm.status(root)
    assert state["active"] and state["consistent"], state


def test_git_ref_updates_are_refused_in_production(root):
    pm.enable(root, operator="operator")
    for args in (["commit", "--allow-empty", "-m", "x"], ["branch", "b"], ["tag", "t"],
                 ["checkout", "-b", "dev"]):
        result = _git(root, *args)
        assert result.returncode != 0 and "reference-transaction" in result.stderr, args


def test_the_agent_rules_deny_edits_and_development_git(root):
    pm.enable(root, operator="operator")
    workspace = json.loads((root.parent / ".claude" / "settings.json").read_text())["permissions"]
    local = json.loads((root / ".claude" / "settings.local.json").read_text())["permissions"]
    for rule in ("Edit(/OpenMontage/lib/**)", "Edit(/Channels/**)", "Bash(git reset*)",
                 "Bash(git clean*)", "PowerShell(git push*)", "Bash(attrib*)"):
        assert rule in workspace["deny"], rule
    assert "Edit(/lib/**)" in local["deny"] and "Bash(git rebase*)" in local["deny"]


def operator_at_console(monkeypatch, answer):
    """Simulate the operator: a real console, and what they type into it."""
    monkeypatch.setattr(pm, "_stdin_is_console", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)


def test_an_agent_cannot_turn_production_mode_off(root, monkeypatch):
    pm.enable(root, operator="operator")
    with pytest.raises(pm.ProductionModeError, match="interactive terminal"):
        pm.disable(root, operator="agent")          # pytest's stdin is not a console
    operator_at_console(monkeypatch, "yes")
    with pytest.raises(pm.ProductionModeError, match="not confirmed"):
        pm.disable(root, operator="operator")
    assert pm.status(root)["active"]
    import inspect

    assert list(inspect.signature(pm.disable).parameters) == ["openmontage", "operator"], \
        "no parameter lets a caller skip the console confirmation"


@pytest.mark.parametrize("stdin", ["null", "pipe"])
def test_the_cli_off_refuses_without_a_console(stdin):
    """NUL (what an agent's shell attaches) and a piped answer are both refused."""
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "lib.production_mode", "off", "--operator", "agent"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
        stdin=subprocess.DEVNULL if stdin == "null" else None,
        input=None if stdin == "null" else "DISABLE PRODUCTION MODE\n")
    assert result.returncode == 2 and "interactive terminal" in result.stderr, result


def test_the_operator_can_turn_it_off_and_everything_is_restored(root, monkeypatch):
    pm.enable(root, operator="operator")
    operator_at_console(monkeypatch, "DISABLE PRODUCTION MODE")
    pm.disable(root, operator="operator")
    (root / "lib" / "engine.py").write_text("X = 2\n")
    assert _git(root, "commit", "-qam", "dev").returncode == 0
    state = pm.status(root)
    assert state["active"] is False and state["consistent"], state
    assert not (root / ".claude" / "settings.local.json").exists()
    workspace = json.loads((root.parent / ".claude" / "settings.json").read_text())
    assert "permissions" not in workspace


def test_a_platform_defect_checkpoints_reports_and_stops(root):
    projects = root / "projects"
    project = projects / "channel_9999__video_0001"
    write_checkpoint(projects, project.name, "research", "in_progress",
                     {"draft_notes": {"angle": "kept"}}, pipeline_type="relaxation")
    pm.enable(root, operator="operator")
    result = pm.record_platform_defect(project, stage="research", summary="probe crashed",
                                       evidence="Traceback ...")
    assert result["stop"] is True and Path(result["report"]).is_file()
    checkpoint = read_checkpoint(projects, project.name, "research")
    assert checkpoint["status"] == "failed" and "PLATFORM DEFECT" in checkpoint["error"]
    assert checkpoint["artifacts"]["draft_notes"] == {"angle": "kept"}
    assert (root / "lib" / "engine.py").read_text() == "X = 1\n", "the engine was not patched"


def test_the_real_rule_set_covers_the_required_paths_and_commands():
    rules = pm.settings_rules()["workspace"]["deny"]
    for path in ("lib", "tools", "pipeline_defs", "skills", "schemas"):
        assert f"Edit(/OpenMontage/{path}/**)" in rules
    assert "Edit(/Channels/**)" in rules and "Edit(/.claude/**)" in rules
    for cmd in ("reset", "clean", "checkout", "merge", "pull", "push", "rebase"):
        assert f"Bash(git {cmd}*)" in rules and f"PowerShell(git {cmd}*)" in rules


def test_the_executive_producer_carries_the_defect_rule():
    text = (Path(__file__).resolve().parents[2] / "skills" / "pipelines" / "relaxation" /
            "executive-producer.md").read_text(encoding="utf-8")
    assert "platform defect -> checkpoint the current project -> report the defect -> stop" in text
    assert "Never patch the engine during a production run" in text
    assert "record_platform_defect(" in text
