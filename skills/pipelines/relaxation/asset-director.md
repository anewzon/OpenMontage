# Relaxation — Asset Director (`assets` stage)

Produces: `asset_manifest`

You are cataloguing what the operator actually gave you. Everything downstream
depends on this being honest and complete. A creative plan built on a wrong
inventory produces an unbuildable timeline.

## 1. Validate the project

Confirm these exist under the project root: `brief.txt`, `visuals/`, `music/`,
`sfx/`, `overlays/`, `licenses/`, `work/`, `output/`.

Read `proposal_packet` for the approved concept. Note the target duration — you will report usable footage
against it.

## 2. Probe every file

For every file in `visuals/`, `music/`, `sfx/`, `overlays/`, run ffprobe and
record:

**Video** — path, duration, width × height, fps, codec, pixel format, bitrate,
rotation, whether it has an audio stream.

**Audio** — path, duration, sample rate, channels, codec, integrated loudness.

Do not skip files because the name looks unpromising. Operators do not rename
files and you were told not to require it.

## 3. Flag, do not silently fix

Record these as flags on the asset, and surface them to the operator:

- Below 3840×2160 — usable, but note it. **Never silently upscale.** A 1080p
  clip stretched to 4K looks worse than a 4K timeline with one soft shot.
- Frame rate that is not 30 — note it; the edit director decides conform policy.
- Visible watermark, burned-in logo, or timecode — reject, with reason.
- People as a primary subject, cities, aggressive camera movement — reject by
  default per BRAND.md, unless `brief.txt` overrides.
- Heavy compression artefacts, rolling shutter, or exposure pumping — flag.

## 4. Find the usable sub-ranges

This is the most valuable thing you do. **Do not assume a whole file is usable.**
Stock clips routinely open or close with a camera bump, a focus hunt, or an
exposure shift.

**Ranges come from inspection, not from a formula.** The second test recorded
`usable_in: 0.6` and `usable_out: duration − 0.6` for all 56 clips. A constant
trim applied to every file is not an analysis; it is a default wearing an
analysis's clothes.

For each visual asset, propose one or more usable in/out ranges that are steady,
well-exposed and clean, and record for each range:

| Field | How it is established |
|---|---|
| `subject` | river, canopy, ridge, rain on leaves, … |
| `shot_scale` | wide / medium / detail — **from looking at frames** |
| `camera_motion` | static / drift / pan / tilt / tracking / push_in / pull_out — **measured** |
| `camera_direction` | left / right / up / down / in / out, or null |
| `camera_speed_band` | still / graceful / brisk / aggressive — **measured** |
| `camera_steadiness` | steady / slightly_unsteady / shaky — **measured** |
| `subject_motion` | still / gentle / moderate / strong — **measured, separately** |
| `usable_seconds` | from inspection of this clip |
| `season` | spring / summer / autumn / winter / indeterminate — from frames |
| `weather` | clear / overcast / mist / rain / snow — from frames |
| `light` | dawn / morning / overcast / midday / golden / dusk — from frames |
| `environment` | the specific place-type this belongs to |
| `dominant_colour` | approximate |
| `loop_suspected` | **measured** |
| `planned_role` | hero / advancing / transitional / breathing-room |

**Use the native `frame_sampler` tool** to extract representative frames, then
look at them. **Do not describe footage you have not looked at.**
`lib.camera_motion.sample_frames()` exists only as a fallback for a caller that
already has the module loaded and needs frames alongside a measurement — where
`frame_sampler` does the job, use `frame_sampler`.

**Analyse each source once.** The measurements and frames recorded here are
what every later stage reads; re-probing the same file at scene planning, edit
or compose is wasted work on a long production. If a later stage needs
something this stage did not record, add it here rather than re-analysing
downstream.

### Camera motion is MEASURED, and kept separate from subject motion (binding)

**A fixed camera filming moving water is not a moving-camera shot.** Recording
"motion: yes" because the water moves is the defect this rule exists to
prevent — and the second test did worse than that: the manifest carried no
motion field at all, and the film came out 84% locked-off water shots against
a brief promising a cinematic journey.

Measure both. Record both. Independently.

```python
from lib.camera_motion import analyse_clip, movement_profile

m = analyse_clip(path)
m.camera_motion, m.camera_direction, m.camera_speed_band, m.camera_steadiness
m.subject_motion, m.subject_moving_fraction    # the water, recorded apart
m.is_moving_camera                             # False for locked-off rapids
m.is_relaxation_suitable_movement              # moving, graceful/brisk, steady
m.loop_suspected
```

Why a library helper and not `video_analyzer`: that tool's
`_classify_scene_motion` answers a different question — real video versus a pan
over a still — and it imports `cv2`, **which is not installed on this
machine**, so it returns `motion_type: "unknown"` for every clip here. Keep
using `video_analyzer` for probing and scene detection; take camera motion from
`lib.camera_motion`, which needs only numpy and FFmpeg.

**A clip's title is not evidence.** Never accept footage because its title or
description says "drone", "cinematic", "aerial" or "river". Two of the three
"drone"-titled clips in this channel's existing pool measure as fully
locked-off shots.

**An unanalysed clip is `unknown`, never `static`.** If `analyse_clip` raises,
record that the clip was not screened. Do not default it into a class.

### Report the pool's movement profile

Run `movement_profile()` over the accepted pool and record it under
`metadata.inventory_stats.movement_profile`. State plainly:

- how many clips carry genuine camera movement, and how many of those are
  usable for relaxation (graceful or brisk **and** steady);
- how many are locked-off shots of moving water;
- which distinct camera motions exist — one kind repeated is not variety;
- whether any aerial, gliding or forward-moving footage exists at all.

**If the pool cannot support the approved concept's promised movement, say so
here**, before the Scene Director tries to cast it. That is a procurement
shortfall, not an editing problem. For reference, the second test's pool
measured 47 of 56 static, 7 with any camera movement, 4 usable, no push-ins,
28 loop-suspected.

### Screen for season, light and environment coherence

Record season, weather and light per clip **from frames**, then check the pool
against the approved concept and report the answer:

- Does enough footage actually match the promised season, time of day and
  landscape to build the film that was approved?
- Which accepted clips are **incompatible** with it?

The second test shipped autumn foliage, bare winter trees, bright midday
summer green and a saturated purple sunset inside a film sold as one autumn
morning. It also passed through a road/causeway shot and a shot whose primary
subject is a person — both listed in BRAND.md as avoid-by-default, and both of
which this stage was supposed to reject.

**Reject or deprioritise clips that are technically usable but visually
repetitive.** Five near-identical rocky-stream close-ups are one shot with four
spares; say so and rank them rather than admitting all five.

## 5. Cross-reference licences

For every asset, record provenance and licence evidence from `licenses/`.

**If an asset has no licence evidence, flag it `licence_unverified` and say so
plainly in your report.** Do not quietly include it and do not quietly drop it —
whether to use it is the operator's call, and it is a publishing risk, not an
editorial one.

## 6. Report the footage budget

Compute and state:

- total usable visual minutes
- total music minutes
- total ambience/SFX minutes
- **usable visual minutes ÷ target duration = the reuse factor**

The reuse factor governs what the creative director can honestly promise. Say it
out loud. As rough guidance for relaxation content: a factor above 0.5 allows a
varied edit; around 0.25 means deliberate, spaced re-use with different
grading/framing; below 0.15 means the target duration should come down or more
footage should come in. State which band you are in and recommend accordingly —
do not just hand over a number.

## Classify every clip's native audio

Source clips often carry their own sound. **Do not blanket-keep it and do not
blanket-mute it** — decide per clip, and record the decision.

For each clip with an audio stream, establish: is the sound genuinely
synchronised to what is visible? Is the recording clean? Does it contain
voices, traffic, handling noise, microphone wind, baked-in music or other
contamination? Does it match the environment the scene plan puts it in?

Then classify:

| Class | Meaning |
|---|---|
| `USE` | Clean, synchronised, matches the scene — may join the environmental foundation |
| `USE_AFTER_TREATMENT` | Useful content behind a fixable problem (wind rumble, hiss, a little too hot) |
| `REJECT` | Voices, traffic, handling noise, baked-in music, or sound that contradicts the picture |
| `NO_AUDIO` | No audio stream present |

Clean native environmental sound is often **better** than a library or
generated bed, because it is genuinely the place on screen. Useful native
supporting sound can feed the environmental layer.

**Measure, do not guess a level.** Record each usable clip's actual loudness so
the Edit Director can set a real gain. **Never assign a blanket figure such as
"native audio at 10%"** — that is a guess dressed as a decision.

Note the risk explicitly for anything classified `REJECT`: rejected audio must
not reach the master, and the Edit and Compose Directors rely on this
classification to keep it out.

## Generated audio — music and SFX (paid, budget-capped)

Generated audio is produced **here**, after the real footage has been analysed
and its native audio classified — not at proposal, and not as a stage of its
own. The approved concept says what the episode needs; the footage says what it
already has. Generate only the difference.

### Every paid call runs through the approved budget (binding)

```python
from lib.relaxation_policy import approved_budget_tracker
from tools.tool_registry import registry

tracker = approved_budget_tracker(proposal_packet, project_state_dir)  # cap mode
result = tracker.run_tool(registry.get("suno_music"), inputs, operation="music: <purpose>")
```

`project_state_dir` is the directory holding this project's checkpoints, so
`cost_log.json` sits beside them. `run_tool` estimates, reserves, executes and
reconciles each call, and persists the log at every step.

- **Never call a paid tool's `execute()` directly.** A call that bypasses the
  tracker has no estimate, no reservation and no cap.
- `approved_budget_tracker` refuses to exist without an approved proposal and
  `approval.approved_budget_usd`. No approval, no paid call.
- **`BudgetExceededError` means STOP.** Checkpoint what exists, report the
  spend so far and what remains unmade, and wait for the operator. Do not
  downgrade quality, switch provider or trim the plan on your own.
- **`ApprovalRequiredError` means the tool was not in the approved estimate.**
  That is a provider substitution; it needs the operator, not a workaround.
- **A retry is a new paid call.** It goes through `run_tool` again and is
  charged against the same budget. Stay within the approved retry allowance;
  never loop on a failing generation.
- **A pricing mismatch means STOP too.** When a paid result carries
  `pricing_mismatch` — the provider charged something other than the known
  rate — the tracker refuses further calls to that tool. Report the expected
  and measured charge and wait; only the operator corrects the rate and calls
  `resolve_pricing_mismatch()`.
- A timed-out music generation is **already paid**: recover it with the tool's
  `operation: "fetch"` and its `task_id`, which costs nothing, instead of
  generating again.
- Write every output with an explicit `output_path` under the project
  workspace (`music/`, `sfx/`). Never let a tool fall back to a default path.

### Music — candidates, not a first result

One paid generation returns several candidates, and the tool downloads **all**
of them (`data["candidates"]`). `output_path` holding candidate zero is a
storage position, not a decision.

**Evaluate every candidate** on the same automated screen the Procurement
Director uses for music — metadata, then measured loudness, dynamic range and
spectral balance, then the channel's prohibited-content list — and accept or
reject each one with a recorded reason. Never pay for another generation to
reach a candidate you already have.

Accept against the programme the proposal planned
(`metadata.paid_audio_plan.music`): the target is **accepted unique seconds**,
not a track count.

### Count measured accepted seconds, and stop when the target is met

**A requested length is not accepted music.** Count only the **measured**
length of each **accepted** candidate — never the requested length, never the
provider's reported length, and never a rejected candidate:

```python
from lib.relaxation_policy import account_music_candidates, next_music_request

accounted = account_music_candidates(result.data["candidates"], decisions)
accepted_seconds += accounted["accepted_seconds"]   # ffprobe-measured, accepted only
```

A 360-second request returning an accepted 347 s candidate and a rejected
301 s one contributes **347 s** — not 360, and not 720. If both are good, both
count at their measured lengths. If neither is, the call still cost money;
record both rejection reasons.

**Recalculate before every further call** with `next_music_request(...)`: it
compares the target with what has actually been accepted, checks the request
ceiling, prices the next call with the tool, and checks the tracker's usable
budget. When the target is met it says stop — **do not spend the unused retry
allowance**. The planned request count and retry allowance are a ceiling,
never a quota. Any further call still goes through `tracker.run_tool`.

`generate_music_programme(...)` runs exactly that loop — screen every
candidate through your `evaluate` callback, count measured accepted seconds,
stop on target, ceiling, budget, pricing mismatch or provider failure — and
can resume from the accepted seconds and request count already recorded.
Record the ledger it returns in `metadata.generated_audio`.

### SFX — derived from this episode, never from a list

Decide which SFX this episode needs from, in order: the channel's `BRAND.md`,
the approved concept and its movements, the **actual** visual assets and what
is visible in them, and the native-audio classification above. Name each
source by its purpose in *this* episode. There is no fixed set of sound
categories, no category folder structure, and no default list — another
channel using this pipeline will need entirely different sounds.

**Native audio first.** Where a clip's own audio is `USE` or
`USE_AFTER_TREATMENT` and genuinely matches what is visible, it covers that
scene. Do not generate an equivalent layer just because a provider is
available, and never plan an equivalent native and generated bed to play at
the same time.

**Long-form cost control.** Generate short **loopable** sources
(`loop: true`) for continuous beds, one-shots for occasional detail, and a new
matching source only where the picture genuinely changes. The Edit Director
builds full-length stems from them with overlaps, fades and variation. The
budget is spent on generated **source** seconds, never on final playback
duration.

### Record generated audio as canonical assets

Each accepted file is an ordinary `asset_manifest` entry: `type: music` or
`sfx`, `source_tool` (the tool name), `provider`, `model`, `prompt`,
`cost_usd`, `duration_seconds` and a `generation_summary`. Rejected candidates
are listed in `metadata.generated_audio` with their reasons, so the spend is
explainable. Report the cost log's total against the approved budget.

## Reject, don't force

**Not every downloaded file belongs in the video.** The employee downloaded what
`work/ASSET_LIST.md` asked for; some of it will still be wrong — off-brand,
soft, watermarked, wrong mood, or simply weaker than its neighbours.

Rejecting a clip with a stated reason is doing the job. Forcing every file in
because someone paid for it is how a montage ends up incoherent. Say what you
rejected and why, then re-check that the remaining footage budget still supports
the target duration.

For audio, assess where practical: duration, loudness, ambience type, mood, and
whether it matches the visible environment — a gentle-creek bed under a
waterfall is a mismatch, not a soundtrack.

## Output — the schema-valid shape (binding)

`asset_manifest.assets[]` is a **closed** schema object. Adding a field to an
asset entry fails validation. The canonical fields are:

| Required | Also allowed |
|---|---|
| `id`, `type`, `path`, `source_tool`, `scene_id` | `duration_seconds`, `resolution`, `format`, `cost_usd`, `provider`, `license`, `original_url`, `generation_summary`, `subtype`, `quality_score`, … |

Note **`duration_seconds`**, not `duration`. There is no top-level `duration`
field and inventing one fails validation.

All relaxation-specific per-asset analysis goes in a metadata map keyed by
asset id:

```yaml
metadata:
  asset_analysis:
    pexels_4318716:
      probe: {width: 1920, height: 1080, fps: "24/1", pix_fmt: yuv420p,
              has_audio: true}
      usable_ranges:
        - {in_seconds: 0.6, out_seconds: 18.6, shot_scale: wide,
           camera_motion: tracking, camera_direction: down,
           camera_speed_band: graceful, camera_steadiness: steady,
           camera_displacement_per_second: 0.067,
           subject_motion: strong, subject_moving_fraction: 0.71,
           season: indeterminate, weather: overcast, light: "diffuse overcast",
           environment: "rocky mountain river", dominant_colour: "grey-teal",
           planned_role: hero}
      loop_suspected: false
      native_audio: {classification: USE, measured_lufs: -21.4}
      flags: []
      quality_findings: []
  inventory_stats:
    reuse_factor: 0.62
    movement_profile: {...}       # from lib.camera_motion.movement_profile()
```

Keep aggregate figures — reuse factor, movement profile, totals — in
`metadata.inventory_stats`, and per-asset detail in `metadata.asset_analysis`.
The Scene Director reads both.

Keep analysis files in `work/` — the operator never manages them.

Use the **native analysis tools** in the registry — `video_analyzer`, media
probing, frame sampling, scene detection, audio analysis. Do not write ad-hoc
Python analysis when a native tool exists.

Run `skills/meta/reviewer.md`, then checkpoint the `assets` stage carrying the
`asset_manifest` artifact.
