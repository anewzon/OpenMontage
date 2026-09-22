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

#### Measure the ASSEMBLED stem, not the source files

A stem's loudness after concatenation, crossfading and treatment is not the
mean of its sources. Measured on this installation, a stem built from files
averaging −16.3 LUFS came out at −9.9 LUFS once assembled; a gain derived from
the source mean would have been 6 dB wrong.

Assemble first, measure the assembly, then derive `gain = target − measured`.
`lib/stem_balance.py` provides `measure_stem()` for exactly this.

#### A percentage in a brief is a relationship, not a setting

When a brief says "music 100%, water 40%, ambience 20%", those figures describe
**relative prominence**. They are not input gains and they are not LUFS
targets. Applied literally to raw recordings they invert the hierarchy they
were meant to express, because different recordings arrive at different
loudness.

Convert the relationship into per-role targets relative to a **reference
layer**, then derive gains from the measured built stems. Where the brief also
states an engineering band ("6–8 dB below the music"), the band is the
authority and a derived figure is a starting point to refine.

#### Constrain a group as a group

When a brief constrains several layers **together** — "forest, birds and wind
combined at about 20%" — that allowance belongs to the group, not to each
member. Giving three members the group's full allowance makes their combined
output roughly 5 dB louder than asked, because equal sources sum by power: two
equal stems land ~3 dB hotter than one, three ~4.8 dB hotter.

Distribute the allowance so the members' **power sum** meets it —
`lib.stem_balance.distribute_group()` — and verify the group's achieved level,
not only each member's. A mix in which every layer hits its individual target
while the group sums hot has still failed.

#### Equal LUFS is not equal prominence

Integrated loudness is a useful common currency, not a model of perception. A
broadband bed and a sparse instrument at the same LUFS do not sit at the same
apparent level; bandwidth, density and transient character all move where a
layer seems to be. Measure to make a balance **reproducible**; listen to decide
whether it is **right**.

#### Inspect short-term behaviour, not only the integrated figure

A stem whose integrated loudness is correct can still contain spikes that
dominate the mix. Check the loudest and quietest short-term (3 s) windows and
the spread between them — `measure_stem()` reports both. Transient-heavy
material (birds, gusts) is where this matters most.

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
| What was measured | Source files | Some stems | Every assembled stem, as it appears in the mix |
| Grouped layers | Each member given the group's allowance | Group noticed | Allowance distributed by power; group's achieved level verified |
| Processing | Applied by default | Mostly justified | Each stage solves a named problem |
| Duration | Fixed length, truncated | Roughly aligned | Derived from the approved timeline |
| Loudness | Single pass | Two-pass on WAV | Two-pass, verified on the encoded file |
| Peak reporting | Sample peak reported as true | Mixed | True peak measured and named correctly |
| Honesty | Unrun checks passed | Some caveats | Every unavailable check declared |
