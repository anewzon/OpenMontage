"""Production mode: the engine is frozen while videos are being made.

A production session writes project artifacts and media under ``projects/``.
It must not change the engine that makes them - ``lib/``, ``tools/``,
``pipeline_defs/``, ``skills/``, ``schemas/`` and the other files listed in
`ENGINE_PATHS` - nor shared policy (every channel's ``BRAND.md``, the
architecture contract, the operator guide) or the permission files that
enforce this. And it must not run development git commands.

Four layers, because no single one covers everything (each was verified on
this installation):

1. **Read-only files.** Every protected file gets the read-only attribute: a
   direct write - an editor tool, Python, a shell redirect - fails.
   Git for Windows clears the attribute itself, so this does not stop git.
2. **A ``reference-transaction`` git hook.** Refuses every ref update:
   commit, reset to another commit, checkout/switch of branch, merge, pull,
   rebase, tag, branch. It cannot stop git from rewriting the working tree
   first (an aborted ``reset --hard`` still restores files) or ``git clean``
   deleting ignored files - which is why layer 3 exists.
3. **Agent permission rules.** ``<VidQwik root>/.claude/settings.json`` (the
   production workspace) and an untracked ``OpenMontage/.claude/
   settings.local.json`` deny edits to protected paths, development and
   destructive git commands, and attribute/permission changes.
4. **An operator-only switch.** `disable` runs only from an interactive
   terminal with a typed confirmation; an agent's shell is never a terminal.

The platform-defect rule: a defect found during production is recorded with
`record_platform_defect` - the current project is checkpointed, a defect
report is written, and production stops. The engine is never patched in a
production run.

    python -m lib.production_mode status
    python -m lib.production_mode on --operator "<name>"
    python -m lib.production_mode off --operator "<name>"     # operator terminal only
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

__all__ = [
    "ENGINE_PATHS",
    "POLICY_PATHS",
    "ProductionModeError",
    "disable",
    "enable",
    "protected_files",
    "record_platform_defect",
    "settings_rules",
    "status",
]

OPENMONTAGE = Path(__file__).resolve().parents[1]

#: Relative to the OpenMontage checkout.
ENGINE_PATHS = ("lib", "tools", "pipeline_defs", "skills", "schemas", "styles",
                "remotion-composer/src", ".claude", ".agents", "backlot", "config.yaml",
                ".env", ".env.example", ".gitignore", "CLAUDE.md", "AGENTS.md",
                "AGENT_GUIDE.md", "requirements.txt", "requirements-dev.txt",
                "requirements-gpu.txt", "setup.py", "Makefile")
#: Relative to the VidQwik root (the checkout's parent).
POLICY_PATHS = ("Channels", "ARCHITECTURE.md", "OPERATOR_GUIDE.md", "START_PROJECT.ps1",
                "START_PROJECT.bat", ".claude")
_SKIP_PARTS = {"__pycache__", "node_modules", ".pytest_cache", ".mypy_cache"}
MARKER = ".vidqwik_production_mode.json"
HOOK = "reference-transaction"
_HOOK_TAG = "VIDQWIK PRODUCTION MODE"
_HOOK_BODY = f"""#!/bin/sh
# {_HOOK_TAG}: installed by lib/production_mode.py - do not edit.
if [ "$1" = "prepared" ]; then
  echo "{_HOOK_TAG}: git ref updates are refused while videos are being made." >&2
  echo "The operator turns production mode off to develop (python -m lib.production_mode off)." >&2
  exit 1
fi
exit 0
"""
#: Git subcommands a production session never runs.
DENIED_GIT = ("reset", "clean", "checkout", "switch", "restore", "merge", "pull", "push",
              "rebase", "stash", "commit", "cherry-pick", "revert", "am", "apply", "rm", "mv",
              "tag", "branch", "update-ref", "worktree", "gc", "prune", "filter-branch", "-C")
DENIED_SHELL = ("attrib", "icacls", "takeown", "chmod", "Set-ItemProperty", "Remove-ItemProperty")


class ProductionModeError(RuntimeError):
    """Production mode cannot be changed or is inconsistent."""


def _roots(openmontage: Optional[Path]) -> tuple[Path, Path]:
    om = Path(openmontage or OPENMONTAGE).resolve()
    return om, om.parent


def _files_under(base: Path, rel: str) -> Iterable[Path]:
    path = base / rel
    if path.is_file():
        yield path
    elif path.is_dir():
        for child in path.rglob("*"):
            if child.is_file() and not (_SKIP_PARTS & set(child.relative_to(base).parts)):
                yield child


def protected_files(openmontage: Optional[Path] = None) -> list[Path]:
    """Every file production mode makes read-only."""
    om, root = _roots(openmontage)
    files = [f for rel in ENGINE_PATHS for f in _files_under(om, rel)]
    files += [f for rel in POLICY_PATHS for f in _files_under(root, rel)]
    marker = root / MARKER
    if marker.is_file():
        files.append(marker)
    return sorted(set(files))


def _set_readonly(path: Path, readonly: bool) -> None:
    mode = path.stat().st_mode
    os.chmod(path, (mode & ~stat.S_IWRITE & ~stat.S_IWGRP & ~stat.S_IWOTH) if readonly
             else (mode | stat.S_IWRITE))


def settings_rules() -> dict[str, dict[str, list[str]]]:
    """The deny rules for the workspace and for the checkout's local settings."""
    shell = [f"{tool}(git {cmd}*)" for tool in ("Bash", "PowerShell") for cmd in DENIED_GIT]
    shell += [f"{tool}({cmd}*)" for tool in ("Bash", "PowerShell") for cmd in DENIED_SHELL]
    workspace = [f"Edit(/OpenMontage/{p}/**)" for p in ENGINE_PATHS]
    workspace += [f"Edit(/OpenMontage/{p})" for p in ENGINE_PATHS]
    workspace += [f"Edit(/{p}/**)" for p in POLICY_PATHS] + [f"Edit(/{p})" for p in POLICY_PATHS]
    workspace += [f"Edit(/{MARKER})"]
    checkout = [f"Edit(/{p}/**)" for p in ENGINE_PATHS] + [f"Edit(/{p})" for p in ENGINE_PATHS]
    return {"workspace": {"deny": workspace + shell}, "checkout": {"deny": checkout + shell}}


def _hooks_dir(om: Path) -> Optional[Path]:
    git = om / ".git"
    if git.is_dir():
        return git / "hooks"
    if git.is_file():   # a worktree: hooks live in the common dir
        common = Path(git.read_text().split("gitdir:", 1)[1].strip()).parents[1]
        return common / "hooks"
    return None


def _write_settings(path: Path, deny: list[str]) -> None:
    data: dict[str, Any] = {}
    if path.is_file():
        _set_readonly(path, False)
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    perms = data.setdefault("permissions", {})
    existing = [r for r in perms.get("deny", []) if r not in deny]
    perms["deny"] = existing + deny
    data["_vidqwik_production_mode"] = ("deny rules written by lib/production_mode.py; "
                                        "remove only through `production_mode off`")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))


def _strip_settings(path: Path, deny: list[str]) -> None:
    """Remove exactly the rules `_write_settings` added; keep everything else."""
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    perms = data.get("permissions") or {}
    perms["deny"] = [r for r in perms.get("deny", []) if r not in set(deny)]
    if not perms["deny"]:
        perms.pop("deny")
    if not perms:
        data.pop("permissions", None)
    data.pop("_vidqwik_production_mode", None)
    path.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))


def enable(openmontage: Optional[Path] = None, *, operator: str) -> dict[str, Any]:
    """Freeze the engine and shared policy for production."""
    if not operator:
        raise ProductionModeError("enable records who turned production mode on")
    om, root = _roots(openmontage)
    rules = settings_rules()
    _write_settings(root / ".claude" / "settings.json", rules["workspace"]["deny"])
    _write_settings(om / ".claude" / "settings.local.json", rules["checkout"]["deny"])
    hooks = _hooks_dir(om)
    if hooks is not None:
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / HOOK).write_bytes(_HOOK_BODY.encode("utf-8"))
    marker = root / MARKER
    if marker.is_file():
        _set_readonly(marker, False)
    info = {"active": True, "enabled_at": datetime.now(timezone.utc).isoformat(),
            "enabled_by": operator, "openmontage": str(om)}
    marker.write_bytes((json.dumps(info, indent=2) + "\n").encode("utf-8"))
    files = protected_files(om)
    for path in files:
        _set_readonly(path, True)
    return {**info, "protected_files": len(files), "hook": str(hooks / HOOK) if hooks else None}


CONFIRM_PHRASE = "DISABLE PRODUCTION MODE"


def _stdin_is_console() -> bool:
    """True only when stdin is a real interactive console.

    ``isatty()`` is not enough on Windows: it reports the NUL device - which is
    what an agent's shell tool attaches to stdin - as a terminal.
    ``GetConsoleMode`` succeeds only for a genuine console handle, never for
    NUL, a file or a pipe.
    """
    if os.name != "nt":
        return sys.stdin is not None and sys.stdin.isatty()
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(handle),
                                                          ctypes.byref(mode)))
    except (OSError, ValueError, AttributeError):
        return False


def disable(openmontage: Optional[Path] = None, *, operator: str) -> dict[str, Any]:
    """Unfreeze - an operator decision, made at a real console.

    Refuses unless stdin is a genuine interactive console and the operator
    types the confirmation phrase there. An agent's shell never has a console
    stdin, so an agent cannot get past a restriction by turning production
    mode off.
    """
    if not _stdin_is_console():
        raise ProductionModeError(
            "production mode can only be turned off by the operator at an interactive terminal")
    confirm = input(f"Type {CONFIRM_PHRASE!r} to allow engine changes: ")
    if confirm.strip() != CONFIRM_PHRASE or not operator:
        raise ProductionModeError("not confirmed - production mode stays on")
    om, root = _roots(openmontage)
    for path in protected_files(om):
        _set_readonly(path, False)
    hooks = _hooks_dir(om)
    hook = hooks / HOOK if hooks else None
    if hook is not None and hook.is_file() and _HOOK_TAG in hook.read_text(errors="replace"):
        hook.unlink()
    local = om / ".claude" / "settings.local.json"
    if local.is_file():
        local.unlink()
    _strip_settings(root / ".claude" / "settings.json", settings_rules()["workspace"]["deny"])
    marker = root / MARKER
    info = {"active": False, "disabled_at": datetime.now(timezone.utc).isoformat(),
            "disabled_by": operator}
    marker.write_bytes((json.dumps(info, indent=2) + "\n").encode("utf-8"))
    return info


def status(openmontage: Optional[Path] = None) -> dict[str, Any]:
    """Whether production mode is on, and whether every layer is actually in place."""
    om, root = _roots(openmontage)
    marker = root / MARKER
    info = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {"active": False}
    files = protected_files(om)
    writable = [str(p) for p in files if os.access(p, os.W_OK)]
    hooks = _hooks_dir(om)
    hook = hooks / HOOK if hooks else None
    hook_ok = bool(hook and hook.is_file() and _HOOK_TAG in hook.read_text(errors="replace"))
    rules = settings_rules()
    settings_ok = {}
    for key, path in (("workspace", root / ".claude" / "settings.json"),
                      ("checkout", om / ".claude" / "settings.local.json")):
        try:
            deny = set(json.loads(path.read_text(encoding="utf-8"))["permissions"]["deny"])
            settings_ok[key] = set(rules[key]["deny"]) <= deny
        except (OSError, ValueError, KeyError):
            settings_ok[key] = False
    layers = {"read_only": not writable, "git_hook": hook_ok, **{
        f"settings_{k}": v for k, v in settings_ok.items()}}
    return {**info, "protected_files": len(files), "writable_protected_files": writable[:10],
            "layers": layers,
            "consistent": all(layers.values()) if info.get("active") else not any(
                [hook_ok, settings_ok["checkout"], settings_ok["workspace"]])}


# --------------------------------------------------------------------------
# platform defect -> checkpoint the project -> report -> stop
# --------------------------------------------------------------------------


def record_platform_defect(project_dir: Path, *, stage: str, summary: str,
                           evidence: str, pipeline_dir: Optional[Path] = None) -> dict[str, Any]:
    """Record a platform defect found during production, and stop.

    Writes ``work/defects/<utc>_<stage>.md``, then re-writes the stage's
    checkpoint as ``failed`` - keeping its artifacts - with the defect as the
    error, so the project resumes exactly where it stopped once the engine is
    fixed in a separate development session. Never patch the engine here.
    """
    from lib.checkpoint import read_checkpoint, write_checkpoint

    project_dir = Path(project_dir)
    pipeline_dir = Path(pipeline_dir) if pipeline_dir else project_dir.parent
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = project_dir / "work" / "defects" / f"{stamp}_{stage}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes((
        f"# Platform defect - production stopped\n\n"
        f"- project: `{project_dir.name}`\n- stage: `{stage}`\n- recorded: {stamp}\n\n"
        f"## Summary\n\n{summary}\n\n## Evidence\n\n{evidence}\n\n"
        "## Next\n\nThe engine was NOT patched. Fix it in a development session with "
        "production mode off, then resume this project from its checkpoint.\n"
    ).encode("utf-8"))
    current = read_checkpoint(pipeline_dir, project_dir.name, stage) or {}
    checkpoint = write_checkpoint(
        pipeline_dir, project_dir.name, stage, "failed", current.get("artifacts") or {},
        error=f"PLATFORM DEFECT - production stopped: {summary}",
        metadata={**(current.get("metadata") or {}),
                  "platform_defect": {"report": str(report), "summary": summary,
                                      "recorded_at": stamp}})
    return {"report": str(report), "checkpoint": str(checkpoint), "stop": True}


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m lib.production_mode")
    parser.add_argument("action", choices=("status", "on", "off"))
    parser.add_argument("--operator", default="")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            result = status()
        elif args.action == "on":
            result = enable(operator=args.operator)
        else:
            result = disable(operator=args.operator)
    except ProductionModeError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
