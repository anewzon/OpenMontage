"""Locate VidQwik files that live beside the OpenMontage checkout.

Channel policy (``Channels/<id>/BRAND.md``) sits in the VidQwik root, outside
this repository. A checkout's parent is that root; a git worktree may sit
deeper. Searching the ancestors finds it from either, so contract tests that
read channel policy run in every checkout where it exists instead of
silently skipping in worktrees.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def channel_brand(channel_id: str = "channel_0001") -> Path:
    """Path to a channel's BRAND.md (possibly absent: callers skip on that)."""
    for base in ROOT.parents:
        candidate = base / "Channels" / channel_id / "BRAND.md"
        if candidate.is_file():
            return candidate
    return ROOT.parent / "Channels" / channel_id / "BRAND.md"
