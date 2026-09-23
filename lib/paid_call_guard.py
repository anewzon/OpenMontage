"""The last line of defence for paid generation in governed projects.

A paid audio tool calls `check_paid_call()` immediately before it spends. In a
governed project the call is allowed only while `ApprovedBudgetTracker.run_tool`
is executing it (`active_call`), and only for the exact tool and output path the
pipeline policy granted (`grant`). A direct ``tool.execute()``, a plain
``CostTracker.run_tool()`` or an approved tracker used without a policy grant is
refused before any request leaves the machine.

Which projects are governed is decided by OpenMontage's own attribution
(`lib.events.infer_project_dir`) and the project's ``project.json``:

- a project whose ``pipeline_type`` is in `GOVERNED_PIPELINES` - governed
- a directory under the projects root with no readable ``pipeline_type`` - governed
- an output path outside the projects root (unattributable) - governed, since
  nothing would record or cap that spend
- a project of any other pipeline - not governed; upstream behaviour is kept

This is an in-process guard. It stops accidental and improvised bypasses; it
cannot stop code that deliberately imitates the tracker.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

__all__ = [
    "GOVERNED_PIPELINES",
    "PaidCallNotAuthorized",
    "active_call",
    "check_paid_call",
    "current_grant",
    "governing_project",
    "grant",
    "grant_matches",
]

#: Pipelines whose paid generation must run through their approved policy.
GOVERNED_PIPELINES = frozenset({"relaxation"})

#: Set by a pipeline policy for exactly one paid call it has authorised.
_GRANT: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar(
    "paid_call_grant", default=None)
#: Set by ApprovedBudgetTracker.run_tool while the reserved call executes.
_ACTIVE: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar(
    "paid_call_active", default=None)


class PaidCallNotAuthorized(RuntimeError):
    """A paid call in a governed project was not authorised by its policy."""


def _resolved(path: Any) -> Optional[Path]:
    if not path:
        return None
    try:
        return Path(path).resolve()
    except (OSError, ValueError):
        return None


def governing_project(inputs: Mapping[str, Any]) -> tuple[bool, Optional[Path], str]:
    """(governed, project_dir, why) for a tool call's inputs."""
    from lib.events import infer_project_dir

    project = infer_project_dir(dict(inputs))
    if project is None:
        return True, None, "the output is outside the projects root, so nothing would record or cap it"
    try:
        pipeline = json.loads((project / "project.json").read_text(encoding="utf-8")).get(
            "pipeline_type")
    except (OSError, ValueError):
        pipeline = None
    if pipeline is None:
        return True, project, f"{project.name} has no readable pipeline_type"
    if pipeline in GOVERNED_PIPELINES:
        return True, project, f"{project.name} is a {pipeline} project"
    return False, project, f"{project.name} is a {pipeline} project (not governed)"


def check_paid_call(tool_name: str, inputs: Mapping[str, Any]) -> None:
    """Raise `PaidCallNotAuthorized` unless this paid call may spend now."""
    governed, _project, why = governing_project(inputs)
    if not governed:
        return
    active = _ACTIVE.get()
    target = _resolved(inputs.get("output_path"))
    if active and active.get("tool") == tool_name and target is not None \
            and _resolved(active.get("output_path")) == target:
        return
    raise PaidCallNotAuthorized(
        f"{tool_name} paid generation refused: {why}. Paid audio in a governed project runs "
        "only through the pipeline policy (generate_music_programme / generate_sfx_source), "
        "which reserves it on the approved budget tracker - never tool.execute() or an "
        "ad-hoc run_tool(). Nothing was sent to the provider."
    )


@contextlib.contextmanager
def grant(*, tool: str, operation: str, output_path: Any, **facts: Any) -> Iterator[dict]:
    """Authorise exactly one paid call: this tool, this operation, this output."""
    token = _GRANT.set({"tool": tool, "operation": operation,
                        "output_path": str(output_path), **facts})
    try:
        yield _GRANT.get()
    finally:
        _GRANT.reset(token)


def current_grant() -> Optional[dict[str, Any]]:
    return _GRANT.get()


def grant_matches(granted: Optional[Mapping[str, Any]], tool_name: str, operation: str,
                  inputs: Mapping[str, Any]) -> bool:
    return bool(granted) and granted.get("tool") == tool_name \
        and granted.get("operation") == operation \
        and _resolved(granted.get("output_path")) is not None \
        and _resolved(granted.get("output_path")) == _resolved(inputs.get("output_path"))


@contextlib.contextmanager
def active_call(granted: Mapping[str, Any]) -> Iterator[None]:
    """Mark a granted, reserved call as executing (used by the approved tracker)."""
    token = _ACTIVE.set(dict(granted))
    try:
        yield
    finally:
        _ACTIVE.reset(token)
