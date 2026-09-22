"""Duration and chunking policy for the relaxation pipeline.

The numbers live in `pipeline_defs/relaxation.yaml` under `metadata.duration`
and `metadata.chunking`. This module reads them from there rather than
restating them, so the manifest stays the single source of truth and a test
cannot pass against a policy the manifest no longer declares.

Why a module rather than prose: "validate 60 <= target_duration_seconds <=
18000" in a Director is an instruction an agent may skip. A function that
raises is a contract that can be tested.

    from lib.relaxation_policy import validate_duration, chunking_required

    validate_duration(900)          # ok
    validate_duration(59)           # DurationOutOfRange
    chunking_required(900)          # False - 15 minutes, chunking optional
    chunking_required(1800)         # True  - 30 minutes, chunk plan required

It also holds the paid-audio budget contract: the proposal-time estimate and
the approved-budget cap every paid call runs under. Pricing is never restated
here - every figure comes from the provider tool's own `estimate_cost()` - and
the cap is OpenMontage's own `CostTracker` in `cap` mode.

    plan = plan_paid_audio(target_duration_seconds=120, music={...}, sfx={...})
    plan["cost_estimate"]          # -> proposal_packet.cost_estimate
    print(budget_summary(plan))    # what the operator approves
    tracker = approved_budget_tracker(proposal_packet, project_dir)
    tracker.run_tool(tool, inputs, operation="...")   # estimate/reserve/execute/reconcile
"""

from __future__ import annotations

import functools
import math
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

__all__ = [
    "BudgetNotApproved",
    "account_music_candidates",
    "generate_music_programme",
    "generation_duration_strategy",
    "next_music_request",
    "DurationOutOfRange",
    "PaidCostUnavailable",
    "approved_budget_tracker",
    "budget_summary",
    "plan_paid_audio",
    "chunking_required",
    "duration_bounds",
    "chunk_threshold_seconds",
    "load_manifest",
    "validate_duration",
]

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "pipeline_defs" / "relaxation.yaml"


class DurationOutOfRange(ValueError):
    """Raised when a requested duration falls outside the supported range.

    Deliberately an error rather than a clamp. A 5-hour request that quietly
    becomes 3 hours, or a 30-second request that quietly becomes 60, is a
    production the operator did not ask for.
    """


@functools.lru_cache(maxsize=1)
def load_manifest(path: Optional[str] = None) -> Mapping[str, Any]:
    """Parse the relaxation manifest once."""
    return yaml.safe_load(Path(path or MANIFEST_PATH).read_text(encoding="utf-8"))


def _policy(manifest: Optional[Mapping[str, Any]], key: str) -> Mapping[str, Any]:
    data = (manifest or load_manifest()).get("metadata", {}).get(key)
    if not data:
        raise RuntimeError(
            f"relaxation.yaml no longer declares metadata.{key}; the pipeline's "
            f"{key} policy has no source of truth"
        )
    return data


def duration_bounds(manifest: Optional[Mapping[str, Any]] = None) -> tuple[int, int]:
    """(min_seconds, max_seconds) the pipeline supports."""
    policy = _policy(manifest, "duration")
    return int(policy["min_seconds"]), int(policy["max_seconds"])


def chunk_threshold_seconds(manifest: Optional[Mapping[str, Any]] = None) -> int:
    """Timeline length at which a chunk plan becomes required."""
    return int(_policy(manifest, "chunking")["threshold_seconds"])


def validate_duration(
    target_duration_seconds: float,
    manifest: Optional[Mapping[str, Any]] = None,
) -> float:
    """Return the duration, or raise `DurationOutOfRange`.

    Call this at the proposal stage. Surfacing the problem is the point; do not
    catch this and substitute a nearby value.
    """
    low, high = duration_bounds(manifest)
    if not low <= target_duration_seconds <= high:
        raise DurationOutOfRange(
            f"target_duration_seconds={target_duration_seconds} is outside the "
            f"supported range [{low}, {high}] "
            f"({low / 60:.0f} minute(s) to {high / 3600:.0f} hours). "
            "Surface this to the operator; do not clamp it."
        )
    return float(target_duration_seconds)


def chunking_required(
    timeline_seconds: float,
    manifest: Optional[Mapping[str, Any]] = None,
) -> bool:
    """True when `metadata.chunk_plan[]` is mandatory for this timeline.

    Below the threshold chunking is *optional* — permitted when render
    complexity calls for it, but never imposed. A 60-second piece does not get
    a chunk plan.
    """
    return timeline_seconds > chunk_threshold_seconds(manifest)


# --------------------------------------------------------------------------
# Paid-audio budget policy
# --------------------------------------------------------------------------
#
# Channel-neutral on purpose. How much unique music a production needs, which
# SFX an episode needs and how generous the retry allowance is are all inputs:
# they come from the channel's BRAND.md, the approved concept and the actual
# footage. Nothing below knows a channel, a subject or a sound category.

#: Approval states under which paid generation may proceed.
_APPROVED = ("approved", "approved_with_changes")


class PaidCostUnavailable(RuntimeError):
    """A paid operation cannot be priced safely, so it must not be planned."""


class BudgetNotApproved(RuntimeError):
    """No operator-approved paid budget exists for this production."""


def _tool(name: str) -> Any:
    from tools.tool_registry import registry

    registry.ensure_discovered()
    tool = registry.get(name)
    if tool is None:
        raise PaidCostUnavailable(f"tool {name!r} is not registered")
    return tool


def _unit_cost(tool: Any, inputs: Mapping[str, Any]) -> float:
    try:
        return float(tool.estimate_cost(dict(inputs)))
    except Exception as exc:  # the tool's own reason says what to confirm
        raise PaidCostUnavailable(
            f"{tool.name} cannot be priced: {exc} Paid production is blocked "
            "until this is resolved - never make an unpriced call to find out."
        ) from exc


def _require_fraction(value: Any, name: str) -> float:
    if value is None:
        raise ValueError(f"{name} must be stated explicitly; there is no default")
    value = float(value)
    if not 0 <= value <= 2:
        raise ValueError(f"{name} must be within [0, 2]")
    return value


def plan_paid_audio(
    *,
    target_duration_seconds: float,
    music: Optional[Mapping[str, Any]] = None,
    sfx: Optional[Mapping[str, Any]] = None,
    video_sourcing: str = "licensed_manual",
    budget_cap_usd: Optional[float] = None,
) -> dict[str, Any]:
    """Estimate every paid audio generation BEFORE anything is spent.

    ``music`` (omit for a production without generated music)::

        tool                        registry name of the music provider
        tool_inputs                 inputs priced per generation (model, ...)
        unique_music_seconds        accepted unique programme to generate
        seconds_per_generation      optional requested length of each call;
                                    derived when omitted (see below)
        generation_duration_reason  required when a shorter-than-efficient
                                    length is chosen on fixed pricing
        candidates_per_generation   candidates one paid request returns
        accepted_per_generation     candidates expected to pass selection
        retry_allowance             extra requests, as a fraction

    ``sfx`` (omit when native audio covers the episode)::

        tool                        registry name of the SFX provider
        tool_inputs                 inputs shared by every source (model, ...)
        sources                     [{purpose, duration_seconds, count}]
        retry_allowance             extra generations, as a fraction

    Music is planned as a PROGRAMME, not minute-for-minute: unique seconds are
    the input, and the runtime beyond them is reprised in the edit. When
    ``seconds_per_generation`` is omitted it is derived from the tool's own
    contract (`generation_duration_strategy`): on fixed per-generation pricing
    the longest permitted request is chosen, because it minimises paid calls.
    The requested length only sizes the estimate - after generation, only the
    MEASURED length of accepted candidates counts (`account_music_candidates`).
    Generated SFX is planned in SOURCE seconds; long beds are built from
    loopable sources.

    Returns ``cost_estimate`` (schema-valid for ``proposal_packet``) and
    ``metadata`` (the arithmetic, for ``proposal_packet.metadata.paid_audio_plan``).
    Raises `PaidCostUnavailable` when any paid line cannot be priced.
    """
    validate_duration(target_duration_seconds)
    line_items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"target_duration_seconds": float(target_duration_seconds)}

    if music:
        tool = _tool(music["tool"])
        unique = float(music["unique_music_seconds"])
        if not 0 < unique <= float(target_duration_seconds):
            raise ValueError(
                "unique_music_seconds must be > 0 and no longer than the production"
            )
        base_inputs = dict(music.get("tool_inputs") or {})
        strategy = _duration_choice(tool, base_inputs, music)
        per_gen = strategy["selected_seconds"]
        candidates = int(music["candidates_per_generation"])
        accepted = float(music["accepted_per_generation"])
        if per_gen <= 0 or candidates < 1 or not 0 < accepted <= candidates:
            raise ValueError("music generation parameters are inconsistent")
        allowance = _require_fraction(music.get("retry_allowance"), "music.retry_allowance")
        inputs = {**base_inputs, "duration_seconds": per_gen}
        unit = _unit_cost(tool, inputs)
        base = math.ceil(unique / (per_gen * accepted))
        retry = math.ceil(base * allowance)
        line_items.append({
            "tool": tool.name, "operation": "music generation", "quantity": base,
            "estimated_usd": round(base * unit, 4),
            "notes": f"{unique:.0f}s accepted unique programme / ({per_gen:.0f}s x "
                     f"{accepted:g} accepted per request); {candidates} candidates each",
        })
        if retry:
            line_items.append({
                "tool": tool.name, "operation": "music retry/rejection allowance",
                "quantity": retry, "estimated_usd": round(retry * unit, 4),
                "notes": f"{allowance:.0%} of base requests; every retry is a new paid request",
            })
        models = (tool.get_info().get("models") or {})
        meta["music"] = {
            "tool": tool.name, "provider": tool.provider,
            "model": inputs.get("model") or models.get("default"),
            "unique_music_seconds": unique,
            "reprised_seconds": round(float(target_duration_seconds) - unique, 3),
            "seconds_per_generation": per_gen,
            "generation_strategy": strategy,
            "candidates_per_generation": candidates,
            "accepted_per_generation": accepted,
            "base_requests": base,
            "retry_requests": retry,
            "usd_per_request": unit,
            "estimated_usd": round((base + retry) * unit, 4),
        }

    if sfx:
        tool = _tool(sfx["tool"])
        shared = dict(sfx.get("tool_inputs") or {})
        sources = [dict(s) for s in (sfx.get("sources") or [])]
        if not sources:
            raise ValueError("sfx.sources is empty; omit sfx instead")
        allowance = _require_fraction(sfx.get("retry_allowance"), "sfx.retry_allowance")
        count = 0
        seconds = 0.0
        base_usd = 0.0
        dearest = 0.0
        for source in sources:
            n = int(source.get("count", 1))
            d = source.get("duration_seconds")
            priced = {**shared, "duration_seconds": d}
            unit = _unit_cost(tool, priced)
            count += n
            seconds += n * float(tool.billable_seconds(priced))
            base_usd += n * unit
            dearest = max(dearest, unit)
        retry = math.ceil(count * allowance)
        line_items.append({
            "tool": tool.name, "operation": "SFX generation", "quantity": count,
            "estimated_usd": round(base_usd, 4),
            "notes": f"{seconds:.1f}s of generated source across {len(sources)} "
                     "episode-specific sources; long beds built from loops, not "
                     "minute-for-minute",
        })
        if retry:
            line_items.append({
                "tool": tool.name, "operation": "SFX retry allowance", "quantity": retry,
                "estimated_usd": round(retry * dearest, 4),
                "notes": f"{allowance:.0%} of generations, priced at the dearest source",
            })
        meta["sfx"] = {
            "tool": tool.name, "provider": tool.provider,
            "model": shared.get("model_id"),
            "generations": count,
            "retry_generations": retry,
            "generated_source_seconds": round(seconds, 3),
            "sources": sources,
            "estimated_usd": round(base_usd + retry * dearest, 4),
        }

    line_items.append({
        "tool": video_sourcing, "operation": "visual footage", "quantity": 0,
        "estimated_usd": 0.0,
        "notes": (
            "licensed manually by a person; subscription/licence cost is "
            "externally managed and excluded from automated API spend"
            if video_sourcing == "licensed_manual"
            else "acquired by native stock tools; no metered API cost"
        ),
    })
    line_items.append({
        "tool": "audio_mixer+video_compose", "operation": "local mixing, render and QC",
        "quantity": 0, "estimated_usd": 0.0, "notes": "local FFmpeg; no metered API cost",
    })

    total = round(sum(item["estimated_usd"] for item in line_items), 4)
    if budget_cap_usd is None:
        verdict = "no_budget_set"
    elif total > budget_cap_usd:
        verdict = "over_budget"
    elif total > 0.9 * budget_cap_usd:
        verdict = "near_limit"
    else:
        verdict = "within_budget"
    cost_estimate: dict[str, Any] = {
        "total_estimated_usd": total,
        "line_items": line_items,
        "budget_verdict": verdict,
    }
    if budget_cap_usd is not None:
        cost_estimate["budget_cap_usd"] = float(budget_cap_usd)
    meta["retry_reserve_usd"] = round(sum(
        i["estimated_usd"] for i in line_items if "allowance" in i["operation"]), 4)
    meta["paid_tools"] = sorted({i["tool"] for i in line_items if i["estimated_usd"] > 0})
    return {"cost_estimate": cost_estimate, "metadata": meta}


def budget_summary(plan: Mapping[str, Any], approved_budget_usd: Optional[float] = None) -> str:
    """The block the operator reads before approving concept AND budget."""
    meta = plan["metadata"]
    est = plan["cost_estimate"]
    music = meta.get("music")
    sfx = meta.get("sfx")
    lines = [f"Target duration: {meta['target_duration_seconds'] / 60:.1f} min"]
    if music:
        strategy = music.get("generation_strategy") or {}
        if strategy.get("pricing_basis") == "fixed_per_generation":
            basis = f"fixed ${music['usd_per_request']:.2f} per generation"
        elif strategy.get("pricing_basis") == "varies_with_duration":
            basis = (f"varies with requested duration (${strategy['cost_at_min_usd']:.2f}"
                     f" at {strategy['range_seconds'][0]:g} s, "
                     f"${strategy['cost_at_max_usd']:.2f} at {strategy['range_seconds'][1]:g} s)")
        else:
            basis = f"${music['usd_per_request']:.2f} per generation"
        lines += [
            "Music generation strategy: "
            f"{music['provider']} {music['model']} - {music['seconds_per_generation']:g} s "
            "requested per paid call",
            f"Pricing basis: {basis}",
            f"Reason: {strategy.get('reason', 'stated by the caller')}",
        ]
        lines += [
            f"Music provider: {music['provider']} via {music['tool']} ({music['model']})",
            f"Music programme target: {music['unique_music_seconds'] / 60:.1f} min unique "
            f"({music['reprised_seconds'] / 60:.1f} min reprised in the edit)",
            f"Expected music generations: {music['base_requests']} + "
            f"{music['retry_requests']} retry allowance",
            f"Estimated music cost: ${music['estimated_usd']:.2f}",
        ]
    else:
        lines.append("Music provider: none (no generated music planned)")
    lines.append("")
    if sfx:
        lines += [
            f"SFX provider: {sfx['provider']} via {sfx['tool']}",
            f"Expected generated SFX source duration: {sfx['generated_source_seconds']:.0f} s",
            f"Expected SFX generations: {sfx['generations']} + "
            f"{sfx['retry_generations']} retry allowance",
            f"Estimated SFX cost: ${sfx['estimated_usd']:.2f}",
        ]
    else:
        lines.append("SFX provider: none (native/licensed audio only)")
    lines += [
        "",
        f"Estimated paid API total: ${est['total_estimated_usd']:.2f}",
        f"Retry reserve: ${meta['retry_reserve_usd']:.2f} (included in the total)",
        "Maximum approved production budget: "
        + (f"${approved_budget_usd:.2f}" if approved_budget_usd is not None
           else "<operator to approve>"),
        "Video: licensed manually - external cost, excluded from API spend",
    ]
    return "\n".join(lines)


def approved_budget_tracker(proposal_packet: Mapping[str, Any], project_dir: Path) -> Any:
    """A `CostTracker` in CAP mode, bounded by the operator-approved budget.

    - refuses to exist without an approved proposal and ``approved_budget_usd``
    - the cap is the approved budget itself (no extra holdback: the retry
      allowance is already an explicit line inside the estimate)
    - only tools priced in the approved ``cost_estimate`` may spend; any other
      paid tool raises ``ApprovalRequiredError`` - no silent substitution
    - persists to ``<project_dir>/cost_log.json``
    """
    from lib.config_model import BudgetMode
    from tools.cost_tracker import CostTracker

    approval = proposal_packet.get("approval") or {}
    if approval.get("status") not in _APPROVED:
        raise BudgetNotApproved(
            f"proposal approval is {approval.get('status')!r}; paid generation needs "
            "an approved proposal"
        )
    budget = approval.get("approved_budget_usd")
    if not isinstance(budget, (int, float)) or budget < 0:
        raise BudgetNotApproved(
            "approval.approved_budget_usd is missing; the operator must approve a "
            "maximum paid budget before any paid generation"
        )
    items = (proposal_packet.get("cost_estimate") or {}).get("line_items") or []
    paid_tools = sorted({i["tool"] for i in items if i.get("estimated_usd", 0) > 0})

    tracker = CostTracker(
        budget_total_usd=float(budget),
        reserve_pct=0.0,
        # The operator approved the whole budget at proposal, so the cap is the
        # gate: an over-large call raises BudgetExceededError, not a per-action
        # approval request.
        single_action_approval_usd=math.inf,
        require_approval_for_new_paid_tool=True,
        mode=BudgetMode.CAP,
        cost_log_path=Path(project_dir) / "cost_log.json",
    )
    # A resumed log must not keep an older budget or an older approval list.
    tracker.budget_total_usd = float(budget)
    tracker._approved_tools = set()
    for name in paid_tools:
        tracker.approve_tool(name)
    tracker._save()
    return tracker


# --------------------------------------------------------------------------
# Generated music: cost-efficient request length, and honest accounting
# --------------------------------------------------------------------------
#
# Generic over the tool contract: the duration range comes from the tool's own
# `input_schema`, the model check from its `dry_run`, and the price from its
# `estimate_cost`. No provider or model is named here.

_EFFICIENT_REASON = "maximum verified duration costs the same as minimum verified duration"


def generation_duration_strategy(tool: Any, tool_inputs: Mapping[str, Any]) -> dict[str, Any]:
    """How request length affects cost for this tool and these inputs.

    Reads ``input_schema.properties.duration_seconds.{minimum,maximum}``,
    confirms the selected model accepts both ends (the tool's own
    ``dry_run``), and prices both ends with ``estimate_cost``. When the two
    prices are equal, the maximum is the cost-efficient request length.
    Raises ValueError when the tool or model offers no explicit duration.
    """
    prop = ((getattr(tool, "input_schema", None) or {}).get("properties") or {}).get(
        "duration_seconds") or {}
    low, high = prop.get("minimum"), prop.get("maximum")
    if low is None or high is None:
        raise ValueError(f"{tool.name} declares no bounded duration_seconds")
    at_low = {**dict(tool_inputs), "duration_seconds": float(low)}
    at_high = {**dict(tool_inputs), "duration_seconds": float(high)}
    cost_low, cost_high = _unit_cost(tool, at_low), _unit_cost(tool, at_high)
    for probe in (at_low, at_high):
        blocker = (tool.dry_run(dict(probe)) or {}).get("blocker")
        if blocker:
            raise ValueError(
                f"{tool.name} cannot request {probe['duration_seconds']:g} s with these "
                f"inputs: {blocker}"
            )
    fixed = abs(cost_high - cost_low) < 1e-9
    return {
        "range_seconds": [float(low), float(high)],
        "cost_at_min_usd": cost_low,
        "cost_at_max_usd": cost_high,
        "pricing_basis": "fixed_per_generation" if fixed else "varies_with_duration",
        "cost_efficient_seconds": float(high) if fixed else None,
    }


def _duration_choice(tool: Any, base_inputs: Mapping[str, Any],
                     music: Mapping[str, Any]) -> dict[str, Any]:
    explicit = music.get("seconds_per_generation")
    reason = (music.get("generation_duration_reason") or "").strip()
    try:
        strategy = generation_duration_strategy(tool, base_inputs)
    except ValueError as exc:
        if explicit is None:
            raise ValueError(
                f"{exc}; state music.seconds_per_generation explicitly"
            ) from exc
        return {"selected_seconds": float(explicit), "source": "caller",
                "pricing_basis": "unknown", "reason": reason or str(exc)}

    note = ("requested length is a generation target; only the measured length "
            "of accepted candidates counts toward the programme")
    if explicit is None:
        if strategy["pricing_basis"] != "fixed_per_generation":
            raise ValueError(
                f"{tool.name} cost rises with requested duration "
                f"(${strategy['cost_at_min_usd']:.4f} -> ${strategy['cost_at_max_usd']:.4f}); "
                "the longest request is not automatically cheapest - state "
                "music.seconds_per_generation and generation_duration_reason"
            )
        return {**strategy, "selected_seconds": strategy["cost_efficient_seconds"],
                "source": "derived_from_tool_contract", "reason": _EFFICIENT_REASON,
                "note": note}

    selected = float(explicit)
    low, high = strategy["range_seconds"]
    if not low <= selected <= high:
        raise ValueError(f"seconds_per_generation must be within [{low:g}, {high:g}]")
    efficient = strategy["cost_efficient_seconds"]
    if efficient is not None and selected < efficient and not reason:
        raise ValueError(
            f"{selected:g} s is shorter than the cost-efficient {efficient:g} s at the "
            "same price per call; record a concrete creative or provider reason in "
            "music.generation_duration_reason"
        )
    return {**strategy, "selected_seconds": selected, "source": "caller",
            "reason": reason or ("cost-efficient maximum" if selected == efficient
                                 else "caller choice on duration-dependent pricing"),
            "note": note}


def account_music_candidates(
    candidates: list[Mapping[str, Any]],
    decisions: Mapping[int, Mapping[str, Any]],
    probe: Optional[Any] = None,
) -> dict[str, Any]:
    """Accepted seconds contributed by ONE paid generation's candidates.

    ``decisions`` maps each candidate ``index`` to ``{"accepted": bool,
    "reason": str}`` - every candidate needs one; nothing is accepted by
    default. An accepted candidate counts at its MEASURED length (ffprobe),
    never the requested or provider-reported length; a rejected or missing
    one counts zero. An accepted candidate that cannot be measured raises.
    """
    if probe is None:
        from tools.analysis.audio_probe import probe_duration as probe
    rows: list[dict[str, Any]] = []
    total = 0.0
    for cand in candidates:
        index = cand.get("index")
        if index not in decisions:
            raise ValueError(f"candidate {index} has no accept/reject decision")
        decision = decisions[index]
        accepted = bool(decision.get("accepted")) and bool(cand.get("downloaded"))
        measured = probe(cand["path"]) if cand.get("path") else None
        if accepted and not measured:
            raise ValueError(f"accepted candidate {index} could not be measured")
        seconds = float(measured) if accepted else 0.0
        total += seconds
        rows.append({
            "index": index,
            "path": cand.get("path"),
            "provider_reported_seconds": cand.get("duration_seconds"),
            "measured_seconds": measured,
            "accepted": accepted,
            "accepted_seconds": seconds,
            "reason": decision.get("reason") or "",
        })
    return {"accepted_seconds": round(total, 3), "candidates": rows}


def next_music_request(
    *,
    target_seconds: float,
    accepted_seconds: float,
    tracker: Any,
    tool: Any,
    inputs: Mapping[str, Any],
    requests_made: int,
    max_requests: int,
) -> dict[str, Any]:
    """Whether ONE more paid generation is justified, from actual progress.

    The planned request count and retry allowance are a ceiling, never a
    quota: once accepted music meets the target, the answer is no.
    """
    remaining = max(0.0, float(target_seconds) - float(accepted_seconds))
    state = {
        "target_seconds": float(target_seconds),
        "accepted_seconds": float(accepted_seconds),
        "remaining_seconds": round(remaining, 3),
        "requests_made": requests_made,
        "max_requests": max_requests,
        "spent_usd": round(tracker.budget_spent_usd, 4),
        "usable_budget_usd": round(tracker.usable_budget_usd, 4),
    }
    if remaining <= 0:
        return {**state, "generate": False, "reason": "target_met"}
    if requests_made >= max_requests:
        return {**state, "generate": False, "reason": "request_ceiling_reached"}
    cost = _unit_cost(tool, inputs)
    state["next_call_usd"] = cost
    if cost > tracker.usable_budget_usd:
        return {**state, "generate": False, "reason": "budget_would_be_exceeded"}
    return {**state, "generate": True, "reason": "more_accepted_music_needed"}


def generate_music_programme(
    *,
    tracker: Any,
    tool: Any,
    inputs: Mapping[str, Any],
    target_seconds: float,
    evaluate: Any,
    max_requests: int,
    accepted_seconds: float = 0.0,
    requests_made: int = 0,
    probe: Optional[Any] = None,
) -> dict[str, Any]:
    """Generate until the accepted unique-music target is met - and no further.

    Every call goes through ``tracker.run_tool`` (estimate, reserve, execute,
    reconcile). ``evaluate(candidates) -> {index: {"accepted", "reason"}}`` is
    the caller's screening of every candidate. Stops on: target met, request
    ceiling, budget, a refused reservation, a pricing mismatch, or a provider
    failure - it never retries a failure on its own. ``accepted_seconds`` and
    ``requests_made`` resume a programme started in an earlier session.
    """
    from tools.cost_tracker import ApprovalRequiredError, BudgetExceededError

    base = Path(inputs["output_path"])
    generations: list[dict[str, Any]] = []
    stop: dict[str, Any] = {}
    while True:
        decision = next_music_request(
            target_seconds=target_seconds, accepted_seconds=accepted_seconds,
            tracker=tracker, tool=tool, inputs=inputs,
            requests_made=requests_made, max_requests=max_requests,
        )
        if not decision["generate"]:
            stop = decision
            break
        n = requests_made + 1
        call_inputs = {**dict(inputs),
                       "output_path": str(base.with_name(f"{base.stem}_g{n:02d}{base.suffix}"))}
        try:
            result = tracker.run_tool(tool, call_inputs, operation=f"music generation {n}")
        except (BudgetExceededError, ApprovalRequiredError) as exc:
            stop = {**decision, "generate": False, "reason": "reservation_refused",
                    "detail": str(exc)}
            break
        requests_made = n
        record: dict[str, Any] = {"request": n, "success": result.success,
                                  "cost_usd": result.cost_usd,
                                  "task_id": (result.data or {}).get("task_id")}
        if result.success:
            cands = (result.data or {}).get("candidates") or []
            accounted = account_music_candidates(cands, evaluate(cands), probe=probe)
            accepted_seconds += accounted["accepted_seconds"]
            record.update(accounted)
        generations.append(record)
        if (result.data or {}).get("pricing_mismatch"):
            stop = {"generate": False, "reason": "pricing_mismatch",
                    "detail": result.data["pricing_mismatch"]}
            break
        if not result.success:
            stop = {"generate": False, "reason": "provider_failure", "detail": result.error}
            break
    return {
        "target_seconds": float(target_seconds),
        "accepted_seconds": round(accepted_seconds, 3),
        "remaining_seconds": round(max(0.0, float(target_seconds) - accepted_seconds), 3),
        "requests_made": requests_made,
        "spent_usd": round(tracker.budget_spent_usd, 4),
        "stop": stop,
        "generations": generations,
    }
