"""Translate a creative loudness *relationship* into measured stem gains.

The problem this solves
----------------------
An operator describes a mix in percentages: "music 100%, flowing water about
40%, forest and birds and wind together about 20%." Those numbers describe a
**creative relationship**, not settings. Three ways of using them are wrong:

1. **As literal input gains.** `volume=1.0 / 0.4 / 0.2` applied to raw source
   files ignores that recordings arrive at wildly different levels. Measured
   on this installation, a river bed came off the wire at -9.9 LUFS while the
   music sat at -16.0: applying 0.4 to the river leaves it *louder* than the
   music it is supposed to sit beneath.

2. **As final LUFS targets.** A percentage is not a loudness unit.

3. **Per layer instead of per group.** Giving forest, birds and wind each a
   20% allowance produces a supporting group roughly 5 dB louder than the 20%
   that was asked for, because three sources at the same level sum. This is
   the defect that makes a nature mix feel crowded.

What this module does instead
-----------------------------
* Reads the **measured loudness of the assembled stem** — after concatenation,
  crossfades and any corrective processing — not the mean of the source files.
  Test 2 established why: a water stem built from files averaging -16.3 LUFS
  measured -9.9 LUFS once assembled. Gain computed from the source mean would
  have been 6 dB wrong.

* Treats a percentage as an **amplitude ratio**, so `offset_db = 20*log10(p)`.
  That is the interpretation that reproduces the operator's own engineering
  bands: 40% -> -7.96 dB, inside the stated 6-8 dB band for water; 20% ->
  -13.98 dB, inside the stated 12-16 dB band for the supporting group. The
  bands remain the authority; the formula is how a starting point is derived
  without guessing, and `clamp_to_band` keeps a refinement honest.

* Distributes a **group** allowance across its members by power, so the
  members' combined loudness equals the group target rather than each member
  reaching it.

* Verifies the result and reports the relationship it actually achieved.

The numbers (which percentages, which bands, which members) are channel taste
and belong in that channel's BRAND.md. This module holds none of them.

Usage
-----
    from lib.stem_balance import BalanceSpec, GroupSpec, solve_balance

    spec = BalanceSpec(
        reference_role="A1-music",
        master_target_lufs=-16.0,
        roles={"A2-water": 0.40},
        groups={
            "ambience": GroupSpec(
                prominence=0.20,
                members={"A3-forest": 0.70, "A4-birds": 0.20, "A5-wind": 0.10},
            )
        },
    )
    plan = solve_balance(spec, measured_built_lufs={...})
    plan.gains_db          # what to hand the mixer
    plan.verify(...)       # after the stems are re-measured
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

#: A stem quieter than this is silence for practical purposes; deriving a gain
#: from it would produce a nonsensical boost.
SILENCE_FLOOR_LUFS = -70.0

#: Guard rails from the operator's brief, in dB below the music reference.
WATER_BAND_DB = (-8.0, -6.0)
SUPPORT_BAND_DB = (-16.0, -12.0)

#: A derived gain beyond this is a sign the stem or the spec is wrong, not
#: something to apply quietly.
MAX_ABS_GAIN_DB = 40.0


class StemBalanceError(ValueError):
    """Raised when a balance cannot be solved from the figures supplied."""


# --------------------------------------------------------------------------
# Specification
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupSpec:
    """A set of roles whose *combined* level is what the brief constrains.

    `prominence` is the group's share of the reference, as an amplitude ratio.
    `members` are relative weights inside the group; only their ratio matters.
    """

    prominence: float
    members: Mapping[str, float]

    def __post_init__(self) -> None:
        if not self.members:
            raise StemBalanceError("a group needs at least one member role")
        if self.prominence <= 0:
            raise StemBalanceError("group prominence must be positive")
        if any(w <= 0 for w in self.members.values()):
            raise StemBalanceError("member weights must be positive")


@dataclass(frozen=True)
class BalanceSpec:
    """The creative relationship, as the channel states it.

    `reference_role` is the 100% layer. Its prominence is 1.0 by definition;
    "100%" means *reference*, not loud, and not maximised.
    """

    reference_role: str
    master_target_lufs: float
    roles: Mapping[str, float] = field(default_factory=dict)
    groups: Mapping[str, GroupSpec] = field(default_factory=dict)

    def all_roles(self) -> list[str]:
        roles = [self.reference_role, *self.roles]
        for group in self.groups.values():
            roles.extend(group.members)
        duplicates = {r for r in roles if roles.count(r) > 1}
        if duplicates:
            raise StemBalanceError(
                f"role(s) appear more than once in the spec: {sorted(duplicates)}"
            )
        return roles

    def group_of(self, role: str) -> Optional[str]:
        for name, group in self.groups.items():
            if role in group.members:
                return name
        return None


# --------------------------------------------------------------------------
# dB helpers
# --------------------------------------------------------------------------


def prominence_to_db(prominence: float) -> float:
    """A prominence percentage read as an amplitude ratio, in dB.

    100% -> 0 dB, 40% -> -7.96 dB, 20% -> -13.98 dB.
    """
    if prominence <= 0:
        raise StemBalanceError("prominence must be positive")
    return 20.0 * math.log10(prominence)


def clamp_to_band(offset_db: float, band: tuple[float, float]) -> float:
    """Keep a derived or refined offset inside a stated engineering band."""
    low, high = min(band), max(band)
    return max(low, min(high, offset_db))


def power_sum_lufs(levels: Iterable[float]) -> float:
    """Combine loudness figures by power, the way overlapping stems combine.

    Two equal stems sum ~3 dB hotter than either alone. Ignoring this is what
    makes a group of supporting layers overshoot its allowance.
    """
    total = sum(10.0 ** (level / 10.0) for level in levels if level > SILENCE_FLOOR_LUFS)
    if total <= 0:
        return SILENCE_FLOOR_LUFS
    return 10.0 * math.log10(total)


def distribute_group(group_target_lufs: float, weights: Mapping[str, float]) -> dict[str, float]:
    """Split a group's loudness allowance across its members by power.

    The members' power sum equals `group_target_lufs`; it is not handed to each
    of them. That difference is the whole point of grouping.
    """
    total_weight = sum(weights.values())
    return {
        role: group_target_lufs + 10.0 * math.log10(weight / total_weight)
        for role, weight in weights.items()
    }


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


def _ffmpeg() -> str:
    pinned = Path(r"D:\VidQwik AI\Tools\ffmpeg-7.1.1-full_build\bin\ffmpeg.exe")
    if pinned.is_file():
        return str(pinned)
    env = os.environ.get("OPENMONTAGE_FFMPEG_DIR")
    if env:
        candidate = Path(env) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("ffmpeg")
    if not found:
        raise StemBalanceError("ffmpeg not found for stem measurement")
    return found


@dataclass
class StemMeasurement:
    """What `ebur128` reports for one assembled stem."""

    path: str
    integrated_lufs: float
    true_peak_dbtp: float
    lra_lu: float
    short_term_max_lufs: Optional[float] = None
    short_term_min_lufs: Optional[float] = None

    @property
    def short_term_span_lu(self) -> Optional[float]:
        if self.short_term_max_lufs is None or self.short_term_min_lufs is None:
            return None
        return round(self.short_term_max_lufs - self.short_term_min_lufs, 2)


def measure_stem(path: str | Path, *, short_term: bool = True) -> StemMeasurement:
    """Measure an ASSEMBLED stem file with `ebur128`.

    Call this on the stem as it will appear in the mix — concatenated,
    crossfaded, treated. Measuring the sources instead is the regression this
    module exists to prevent.

    `short_term` also reports the loudest and quietest 3-second windows, which
    is how a transient problem (a bird spike, a wind gust) shows up in a stem
    whose integrated figure looks correct.
    """
    path = Path(path)
    if not path.is_file():
        raise StemBalanceError(f"no such stem: {path}")

    result = subprocess.run(
        [
            _ffmpeg(),
            "-nostats",
            "-hide_banner",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    log = result.stderr

    summary = log.rsplit("Summary:", 1)
    tail = summary[-1] if len(summary) > 1 else log

    def _grab(pattern: str) -> Optional[float]:
        match = re.search(pattern, tail)
        return float(match.group(1)) if match else None

    integrated = _grab(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
    true_peak = _grab(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")
    lra = _grab(r"LRA:\s*(-?\d+(?:\.\d+)?)\s*LU")

    if integrated is None:
        raise StemBalanceError(
            f"ebur128 reported no integrated loudness for {path.name}; "
            "the file may be silent or unreadable"
        )

    short_max = short_min = None
    if short_term:
        values = [
            float(v)
            for v in re.findall(r"\sS:\s*(-?\d+(?:\.\d+)?)\s", log)
            if float(v) > SILENCE_FLOOR_LUFS
        ]
        if values:
            short_max, short_min = max(values), min(values)

    return StemMeasurement(
        path=str(path),
        integrated_lufs=round(integrated, 2),
        true_peak_dbtp=round(true_peak, 2) if true_peak is not None else float("nan"),
        lra_lu=round(lra, 2) if lra is not None else float("nan"),
        short_term_max_lufs=round(short_max, 2) if short_max is not None else None,
        short_term_min_lufs=round(short_min, 2) if short_min is not None else None,
    )


# --------------------------------------------------------------------------
# Solving
# --------------------------------------------------------------------------


@dataclass
class RolePlan:
    """The decision for one role, with the figures it was derived from."""

    role: str
    group: Optional[str]
    prominence: Optional[float]
    offset_from_reference_db: float
    built_stem_lufs: float
    target_lufs: float
    gain_db: float


@dataclass
class BalancePlan:
    """A complete, reproducible balance decision."""

    reference_role: str
    reference_target_lufs: float
    roles: dict[str, RolePlan]
    group_targets_lufs: dict[str, float]
    predicted_sum_lufs: float
    master_target_lufs: float
    notes: list[str] = field(default_factory=list)

    @property
    def gains_db(self) -> dict[str, float]:
        """What to hand the mixer, per role."""
        return {role: plan.gain_db for role, plan in self.roles.items()}

    @property
    def targets_lufs(self) -> dict[str, float]:
        return {role: plan.target_lufs for role, plan in self.roles.items()}

    def to_metadata(self) -> dict[str, Any]:
        """Shape for `edit_decisions.metadata.mix_balance`.

        Deliberately records the method and the built-stem figure per role, so
        a later episode reproduces the balance instead of rediscovering it —
        and so a reviewer can see the gain was not a provider default.
        """
        return {
            "method": (
                "creative prominence read as an amplitude ratio "
                "(offset_db = 20*log10(prominence)), applied relative to the "
                "reference role; supporting roles solved as a GROUP by power "
                "so their combined level meets the group allowance; per-role "
                "gain = target - MEASURED BUILT STEM loudness"
            ),
            "reference_role": self.reference_role,
            "reference_target_lufs": round(self.reference_target_lufs, 2),
            "master_target_lufs": self.master_target_lufs,
            "predicted_sum_lufs": round(self.predicted_sum_lufs, 2),
            "group_targets_lufs": {
                k: round(v, 2) for k, v in self.group_targets_lufs.items()
            },
            "roles": {
                role: {
                    "group": plan.group,
                    "prominence_of_reference": plan.prominence,
                    "offset_from_reference_db": round(plan.offset_from_reference_db, 2),
                    "built_stem_lufs": round(plan.built_stem_lufs, 2),
                    "target_lufs": round(plan.target_lufs, 2),
                    "gain_db": round(plan.gain_db, 2),
                }
                for role, plan in self.roles.items()
            },
            "notes": self.notes,
        }

    def verify(
        self,
        remeasured_lufs: Mapping[str, float],
        *,
        tolerance_lu: float = 1.0,
    ) -> "BalanceVerification":
        """Check the executed mix against the plan.

        Pass the loudness of each stem measured AFTER its gain was applied.
        The relationship is checked, not just the individual numbers: a mix
        where every role hits its target but the group sums 5 dB hot is still
        the defect this module exists to prevent.
        """
        role_errors: dict[str, float] = {}
        for role, plan in self.roles.items():
            if role not in remeasured_lufs:
                continue
            role_errors[role] = round(remeasured_lufs[role] - plan.target_lufs, 2)

        reference_actual = remeasured_lufs.get(
            self.reference_role, self.reference_target_lufs
        )

        achieved: dict[str, float] = {}
        for role in self.roles:
            if role == self.reference_role or role not in remeasured_lufs:
                continue
            achieved[role] = round(remeasured_lufs[role] - reference_actual, 2)

        group_achieved: dict[str, float] = {}
        for group, target in self.group_targets_lufs.items():
            members = [
                remeasured_lufs[role]
                for role, plan in self.roles.items()
                if plan.group == group and role in remeasured_lufs
            ]
            if members:
                group_achieved[group] = round(
                    power_sum_lufs(members) - reference_actual, 2
                )

        failures = [
            f"{role}: {error:+.2f} LU from its target"
            for role, error in role_errors.items()
            if abs(error) > tolerance_lu
        ]

        return BalanceVerification(
            role_errors_lu=role_errors,
            achieved_offsets_db=achieved,
            achieved_group_offsets_db=group_achieved,
            tolerance_lu=tolerance_lu,
            failures=failures,
        )


@dataclass
class BalanceVerification:
    """The relationship the mix actually achieved."""

    role_errors_lu: dict[str, float]
    achieved_offsets_db: dict[str, float]
    achieved_group_offsets_db: dict[str, float]
    tolerance_lu: float
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures

    def in_band(self, group: str, band: tuple[float, float]) -> bool:
        """Is a group's achieved offset inside its stated engineering band?"""
        if group not in self.achieved_group_offsets_db:
            return False
        low, high = min(band), max(band)
        # A group quieter than the band is a taste question, not a defect;
        # louder than the band is the failure mode that matters.
        return self.achieved_group_offsets_db[group] <= high + self.tolerance_lu

    def to_metadata(self) -> dict[str, Any]:
        return {
            "role_errors_lu": self.role_errors_lu,
            "achieved_offsets_from_reference_db": self.achieved_offsets_db,
            "achieved_group_offsets_from_reference_db": self.achieved_group_offsets_db,
            "tolerance_lu": self.tolerance_lu,
            "measurement_passed": self.passed,
            "failures": self.failures,
            "limitation": (
                "These are MEASUREMENTS. They establish the balance is the one "
                "that was decided; they do not establish that it sounds good. "
                "A subjective listening review is a separate check and must be "
                "reported separately."
            ),
        }


def solve_balance(
    spec: BalanceSpec,
    measured_built_lufs: Mapping[str, float],
    *,
    water_role: Optional[str] = None,
    water_band: tuple[float, float] = WATER_BAND_DB,
    group_bands: Optional[Mapping[str, tuple[float, float]]] = None,
) -> BalancePlan:
    """Derive per-role targets and gains from measured built stems.

    `measured_built_lufs` must come from the ASSEMBLED stems (see
    `measure_stem`). Supplying source-file means reintroduces the error this
    module documents.

    The reference role's target is chosen so the predicted sum of all stems
    lands on `master_target_lufs`. That way the balance is set by the
    relationship and the overall level follows from it — rather than the
    reverse, where final normalisation is asked to repair a balance it cannot
    reach inside.
    """
    group_bands = dict(group_bands or {})
    notes: list[str] = []

    roles = spec.all_roles()
    missing = [r for r in roles if r not in measured_built_lufs]
    if missing:
        raise StemBalanceError(
            f"no measured built-stem loudness for: {sorted(missing)}. "
            "Assemble and measure every stem before solving the balance."
        )

    silent = [
        r for r in roles if measured_built_lufs[r] <= SILENCE_FLOOR_LUFS
    ]
    if silent:
        raise StemBalanceError(
            f"stem(s) measure at or below the silence floor: {sorted(silent)}. "
            "A gain cannot be derived from silence; fix the stem first."
        )

    # --- offsets relative to the reference, before any level is chosen ---
    offsets: dict[str, float] = {spec.reference_role: 0.0}
    prominences: dict[str, Optional[float]] = {spec.reference_role: 1.0}
    role_group: dict[str, Optional[str]] = {spec.reference_role: None}

    for role, prominence in spec.roles.items():
        offset = prominence_to_db(prominence)
        if water_role and role == water_role:
            clamped = clamp_to_band(offset, water_band)
            if abs(clamped - offset) > 0.01:
                notes.append(
                    f"{role}: derived offset {offset:.2f} dB clamped to "
                    f"{clamped:.2f} dB by the stated band {water_band}"
                )
            offset = clamped
        offsets[role] = offset
        prominences[role] = prominence
        role_group[role] = None

    group_offsets: dict[str, float] = {}
    for group_name, group in spec.groups.items():
        offset = prominence_to_db(group.prominence)
        band = group_bands.get(group_name)
        if band:
            clamped = clamp_to_band(offset, band)
            if abs(clamped - offset) > 0.01:
                notes.append(
                    f"group {group_name}: derived offset {offset:.2f} dB "
                    f"clamped to {clamped:.2f} dB by the stated band {band}"
                )
            offset = clamped
        group_offsets[group_name] = offset

        # The group allowance is split by power across its members, so their
        # COMBINED level meets the allowance.
        member_offsets = distribute_group(offset, group.members)
        for role, member_offset in member_offsets.items():
            offsets[role] = member_offset
            prominences[role] = None
            role_group[role] = group_name
        notes.append(
            f"group {group_name}: allowance {offset:.2f} dB split by power "
            "across "
            + ", ".join(
                f"{role} {member_offsets[role] - offset:+.2f} dB"
                for role in group.members
            )
            + " - the members SUM to the allowance rather than each reaching it"
        )

    # --- choose the reference level so the whole mix lands on target ---
    # Sum of relative powers, then solve for the reference's absolute level.
    relative_sum_db = power_sum_lufs(offsets.values())
    reference_target = spec.master_target_lufs - relative_sum_db

    role_plans: dict[str, RolePlan] = {}
    for role in roles:
        target = reference_target + offsets[role]
        gain = target - measured_built_lufs[role]
        if abs(gain) > MAX_ABS_GAIN_DB:
            raise StemBalanceError(
                f"{role} needs {gain:+.1f} dB to reach {target:.1f} LUFS from a "
                f"built stem measuring {measured_built_lufs[role]:.1f} LUFS. "
                "A correction that large means the stem or the spec is wrong; "
                "do not apply it quietly."
            )
        role_plans[role] = RolePlan(
            role=role,
            group=role_group[role],
            prominence=prominences[role],
            offset_from_reference_db=offsets[role],
            built_stem_lufs=measured_built_lufs[role],
            target_lufs=target,
            gain_db=round(gain, 2),
        )

    return BalancePlan(
        reference_role=spec.reference_role,
        reference_target_lufs=reference_target,
        roles=role_plans,
        group_targets_lufs={
            name: reference_target + offset for name, offset in group_offsets.items()
        },
        predicted_sum_lufs=power_sum_lufs(
            plan.target_lufs for plan in role_plans.values()
        ),
        master_target_lufs=spec.master_target_lufs,
        notes=notes,
    )


def spec_from_brand(
    brand_text: str,
    *,
    reference_role: str,
    master_target_lufs: float,
    roles: Mapping[str, float],
    groups: Mapping[str, GroupSpec],
) -> BalanceSpec:
    """Build a spec, asserting the channel file still declares the relationship.

    The percentages live in BRAND.md. This exists so a caller cannot quietly
    carry on using a relationship the channel has stopped stating — which is
    how a "channel standard" drifts into a hard-coded default.
    """
    if "prominence" not in brand_text.lower() and "% of the music" not in brand_text.lower():
        raise StemBalanceError(
            "BRAND.md no longer states the layer prominence relationship; "
            "re-read the channel file before mixing"
        )
    return BalanceSpec(
        reference_role=reference_role,
        master_target_lufs=master_target_lufs,
        roles=roles,
        groups=groups,
    )


def _main(argv: list[str]) -> int:
    """Measure stems given on the command line."""
    if not argv:
        print(__doc__)
        return 2
    out = [measure_stem(path).__dict__ for path in argv]
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
