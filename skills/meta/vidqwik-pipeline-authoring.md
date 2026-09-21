# VidQwik Pipeline Authoring — Meta Skill

## When to Use

Before creating a new VidQwik AI pipeline, a new reusable skill, a production
subsystem, a sourcing mechanism, a state mechanism or a renderer extension.

This is an **architectural guardrail**, not a framework. Its whole job is to
stop each new agent inventing a different VidQwik architecture.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Contract | `D:\VidQwik AI\ARCHITECTURE.md` | The permanent VidQwik rules — **read first** |
| Contract | `AGENT_GUIDE.md`, `PROJECT_CONTEXT.md`, `docs/ARCHITECTURE.md` | Current OpenMontage architecture |
| Protocol | `meta/checkpoint-protocol.md`, `meta/reviewer.md` | Native state and review |
| Reference | `pipeline_defs/`, `skills/pipelines/` | Existing conventions |

## Process

### Step 1: Read the contracts

Read `D:\VidQwik AI\ARCHITECTURE.md`, then the current OpenMontage contracts.
**If they disagree, OpenMontage wins** — and fix `ARCHITECTURE.md`.

### Step 2: Ask whether anything new is needed at all

In order:

1. Can an **existing pipeline** handle this channel or format?
2. Can an **existing meta skill** supply the behaviour?
3. **Does OpenMontage already provide it?**

A new channel is **not** a reason for a new pipeline. Fifty channels in one
niche should share one pipeline and differ only through their `BRAND.md`.

### Step 3: Place the behaviour in the right layer — before writing files

Apply the decision-promotion rule from `ARCHITECTURE.md` §C:

| Applies to… | Goes in… |
|---|---|
| one video | project artifacts / checkpoints |
| one channel | `BRAND.md` · `COMPETITORS.md` · `RESEARCH.md` |
| one format or niche | that pipeline's Director skills |
| several pipelines | an OpenMontage meta skill |
| VidQwik itself | `ARCHITECTURE.md` |

**Decide this before any file exists.** Misplaced behaviour is far harder to
extract later than to place correctly now.

### Step 4: Create a pipeline only for materially different grammar

Justified when research, structure, sourcing, editing **and** audio grammar
genuinely differ. Not justified by a different subject, look or channel.

When it is justified:

- Write a manifest in `pipeline_defs/` validating against the current schema.
- **Prefer canonical stage names** (`research`, `proposal`, `idea`, `script`,
  `scene_plan`, `assets`, `edit`, `compose`, `publish`) — they carry enforced
  canonical artifacts. A non-canonical stage silently bypasses that
  enforcement, so add one only when no canonical stage expresses the step, and
  justify it in the manifest.
- Write one Director skill per stage.
- **Reference existing meta skills; do not copy them.** A Director says what
  *good* means for this format; the meta skill says *how*.
- Do not fork another pipeline and leave duplicated logic. Extract the generic
  part into a meta skill first, then specialise.

### Step 5: Keep identity out of the pipeline

The pipeline must serve **any** channel in its niche. No channel name, no
brand, no competitor, no subject, no fixed resolution or duration baked in.
Those come from `BRAND.md` and the approved proposal, read at runtime.

Test: *could a second channel in this niche use this pipeline unchanged?*
If not, something channel-specific has leaked in.

### Step 6: Keep state native

Canonical artifacts and native checkpoints are authoritative. Resume via
`get_next_stage()`. `write_checkpoint()` already enforces approval gates, stage
prerequisites and artifact schemas.

**Do not add a state file, status field or progress tracker that production
reads.** A second source of truth will eventually disagree with the first.

### Step 7: Promote repetition

If the same instruction appears in two pipelines, **promote it to a meta skill**
and have both reference it. Duplication is how the architecture rots.

### Step 8: Tests

Add the **minimum useful** contract tests — enough to protect the architecture,
not enough to break on rewording. Test structural facts (a skill exists, a
manifest validates, a reference resolves), not exact sentences.

Then **run the full suite** and report the exact result. Do not accept
regressions without explaining them.

### Step 9: Avoid core changes

VidQwik work should need **no OpenMontage core modification**. If you believe
one is required, **stop before making it** and explain why the existing
extension points are insufficient.

### Step 10: Self-evaluate

| Criterion | 1 | 3 | 5 |
|---|---|---|---|
| Reuse first | Built new by default | Considered reuse | Proved nothing existing fits |
| Layer placement | Mixed levels | Mostly right | Every addition in exactly one level |
| Identity isolation | Channel baked in | Some leakage | Pipeline serves any channel in the niche |
| Native state | Parallel tracking added | Mixed | Checkpoints sole authority |
| Tests | None or brittle | Some | Minimum useful, full suite run |

### Step 11: Update the contract

**Any architectural change updates `D:\VidQwik AI\ARCHITECTURE.md` in the same
task.** A change that is not written down will be reinvented differently by the
next agent.

---

## Creating a new channel — the short version

No code is normally needed:

1. Assign the next permanent `channel_NNNN` ID.
2. Choose an existing appropriate pipeline.
3. Create `Channels\channel_NNNN\` with `BRAND.md`, `COMPETITORS.md`,
   `RESEARCH.md`, `assets\branding\`.
4. Write the brand and strategy; add a row to `Channels\CHANNELS.md`.

If a new channel seems to require code, re-read §D of `ARCHITECTURE.md` — it
almost certainly does not.
