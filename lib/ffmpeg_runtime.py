"""Resolve `ffmpeg` / `ffprobe` the way OpenMontage's tools already do.

This is **not** a resolver subsystem and holds no configuration. It is a
three-line wrapper over `shutil.which` that exists so the additive quality
helpers (`camera_motion`, `stem_balance`, `transition_audit`) do not each
repeat the same lookup and the same error text.

The contract is OpenMontage's ordinary command dependency, declared on tools
as `cmd:ffmpeg` / `cmd:ffprobe` and checked in
`tools/base_tool.py::check_dependencies` with `shutil.which(cmd_name)`. These
helpers follow it exactly: **PATH, and nothing else.**

History worth keeping: these helpers previously probed a project-local pinned
FFmpeg build, and an override environment variable, before falling back to
PATH. That made the project the maintainer of its own FFmpeg distribution, let
the helpers silently use a *different* binary from the one the OpenMontage
tools resolved, and let the test suite pass against a runtime production would
not have. Both were removed. Do not add another: if FFmpeg is missing, install
it on the system and let PATH do its job.
"""

from __future__ import annotations

import shutil

__all__ = ["FFmpegNotAvailable", "ffmpeg_path", "ffprobe_path", "is_available"]

#: Mirrors the guidance `BaseTool.install_instructions` gives for command
#: dependencies, so a failure here reads like any other missing-tool failure.
_INSTALL_HINT = (
    "Install FFmpeg and make sure it is on PATH "
    "(Windows: `winget install Gyan.FFmpeg`, then open a new shell). "
    "OpenMontage resolves it as an ordinary `cmd:` dependency; there is no "
    "project-local copy and no override variable."
)


class FFmpegNotAvailable(RuntimeError):
    """Raised when a required FFmpeg binary is not on PATH."""


def _resolve(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise FFmpegNotAvailable(f"Command {name!r} not found. {_INSTALL_HINT}")
    return found


def ffmpeg_path() -> str:
    """Absolute path to `ffmpeg`, or raise `FFmpegNotAvailable`."""
    return _resolve("ffmpeg")


def ffprobe_path() -> str:
    """Absolute path to `ffprobe`, or raise `FFmpegNotAvailable`."""
    return _resolve("ffprobe")


def is_available() -> bool:
    """True when both binaries resolve. For preflight reporting only.

    Callers that need a binary should call `ffmpeg_path()` / `ffprobe_path()`
    and let the error surface, rather than checking this first and guessing.
    """
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
