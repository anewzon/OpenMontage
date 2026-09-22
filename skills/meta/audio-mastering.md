# Audio Mastering — Meta Skill

## When to Use

Whenever a pipeline produces a final mix that must meet a loudness and
true-peak target and survive encoding.

This skill covers **generic audio engineering** that benefits any pipeline. It
holds no channel preference and no format preference: the target numbers, the
musical taste and the layer plan come from the calling Director and the
channel's brand file.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Tool | `audio_mixer` | Multi-track mix, per-track gain/offset/fades |
| Tool | `audio_enhance`, FFmpeg filters | Cleanup and correction |
| Caller | The pipeline's Edit/Compose Director | Targets, layer plan, timeline |

---

## The four measurements — do not confuse them

Reporting the wrong one is a common and consequential error.

| Measurement | What it is | How to get it |
|---|---|---|
| **Sample peak** | Highest sample value | `astats` Peak_level — **not** true peak |
| **True peak** | Highest inter-sample value after reconstruction | `ebur128=peak=true` → `Peak:` |
| **Integrated loudness** | Loudness over the whole programme (LUFS) | `ebur128` → `I:` |
| **Short-term loudness** | Loudness over a 3 s window | `ebur128` → `S:` |
| **Loudness range (LRA)** | Spread of loudness across the programme | `ebur128` → `LRA:` |

**Never report sample peak as true peak.** Inter-sample peaks can exceed
sample peak by more than a dB, which is exactly the margin a ceiling protects.

---

## Process

### Step 1: Gain staging before processing

Set each layer's level so the *unprocessed* sum already sounds right. Fixing a
bad balance with compression afterwards produces a mix that measures fine and
sounds wrong.

**Do not apply a uniform percentage to a class of sources.** "All native audio
at 10%" is a guess, not a decision — measure each source and set its level from
what it actually contains.

### Step 2: Process only to solve a stated problem

EQ, compression and limiting are corrective tools, not default stages. Before
adding one, name the audible or measured problem it fixes.

Watch for: over-compression and pumping · clipped transients · harsh highs
(often 2–6 kHz) · muddy low-frequency buildup when several beds overlap · one
layer masking another.

**Do not apply dialogue-mixing conventions to material with no dialogue.** In
particular, **do not duck one bed under another as if it were narration** —
ducking music under ambience makes the music lurch every time the ambience
swells.

### Step 3: Transitions

Every layer entrance and exit is a fade or a crossfade. A hard in-point on an
ambience bed is audible even at low level.

Where a bed must be extended, crossfade between takes rather than butt-joining
them, and vary which take is used so the repetition is not periodic.

### Step 4: Duration comes from the timeline

**Derive the mix length from the approved edit timeline**, not from a round
number. Building a fixed-length soundtrack and truncating it at mux time
discards the planned ending and is the usual cause of a clipped final fade.

Place fades at real timeline positions, and confirm the last fade is inside the
final duration rather than beyond it.

### Step 5: Loudness — two passes, always

Single-pass `loudnorm` is a dynamic estimator and **will miss its target**.
Always:

1. Measure with `loudnorm=...:print_format=json`.
2. Re-apply passing `measured_I`, `measured_TP`, `measured_LRA`,
   `measured_thresh`, `offset`, and `linear=true`.

Leave headroom below the ceiling: later stages (a filter pass, a lossy encode)
can push peaks back up. Targeting the ceiling exactly tends to overshoot it.

### Step 6: Verify the *encoded* audio

Lossy encoding changes the waveform. **Measure the delivered file, not the
intermediate WAV.** A WAV that met the ceiling can exceed it after AAC.

If the encoded audio fails, fix the audio and remux — **do not re-encode the
video** to correct an audio-only problem.

### Step 7: Mono fold-down and stereo stability

Check the mix summed to mono: phase cancellation between two similar wide
recordings can hollow out a bed that sounded fine in stereo. Many viewers play
on a single speaker.

Keep the stereo image stable — avoid beds that wander or collapse between
sections.

### Step 8: Listen

Measurements confirm a mix is *legal*. They cannot tell you it is *good*.

Sample across the whole duration — beginning, several interior points, and the
ending — and listen for the problems above. **A file that reaches its target
LUFS has not thereby passed.**

Where a check cannot actually be performed in this session, say so and request
a listening review. **Never record a pass for a check that did not run.**

### Step 9: Self-evaluate

| Criterion | 1 | 3 | 5 |
|---|---|---|---|
| Gain staging | Uniform guesses | Some measured | Every layer set from measurement and listening |
| Processing | Applied by default | Mostly justified | Each stage solves a named problem |
| Duration | Fixed length, truncated | Roughly aligned | Derived from the approved timeline |
| Loudness | Single pass | Two-pass on WAV | Two-pass, verified on the encoded file |
| Peak reporting | Sample peak reported as true | Mixed | True peak measured and named correctly |
| Honesty | Unrun checks passed | Some caveats | Every unavailable check declared |
