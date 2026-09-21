# Asset Procurement — Meta Skill

## When to Use

Whenever a pipeline needs **source assets it does not already have** — stock
video, music, ambience or SFX — and a human will license and download them.

This skill teaches **HOW** to discover, evaluate and verify assets from a stock
provider. It does **not** decide *what* a given format needs: that belongs to
the pipeline's Procurement Director, which reads this skill and specialises it.

It is provider-aware but not provider-bound. Envato is the current provider and
has its own profile below; a future provider reuses the same reasoning with a
different profile.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `proposal_packet` | The approved concept — the source of every requirement |
| Channel | `<channel_root>/BRAND.md` | Subject priorities and what to avoid |
| Skill | The calling pipeline's `procurement-director.md` | Format-specific definition of "good" |
| Tools | Browser (search, item pages, previews) | Discovery and verification |
| Protocol | `meta/checkpoint-protocol.md` | The `awaiting_human` gate |

**Channel and format specifics are read at runtime.** Nothing in this skill may
hard-code a subject, season, resolution, duration or channel name.

---

## Process

```
asset requirement → search intent → inspect provider filters → filter profile
→ search → Pass 1 fast screening → Pass 2 creative confirmation
→ exact-item verification → cross-list diversity review
→ procurement list → human licensing/download gate
```

### Step 1: Derive the requirement

From the approved concept, express each requirement as: **what must be visible
or audible**, **why the edit needs it**, **how much**, and **any technical
floor**. Requirements come from the proposal and the Director — never invented
here.

### Step 2: Inspect the provider's actual filters

**Open the provider's search UI and read the filters it currently offers.** Do
not assume a fixed set — providers change them.

### Step 3: Build a filter profile

For each requirement, sort every available filter into three buckets:

| Bucket | Meaning |
|---|---|
| **HARD** | Violating it makes the asset unusable |
| **PREFERRED** | Improves fit; may be relaxed if results dry up |
| **UNSET** | Leave alone — constraining it adds nothing |

Worked example (illustrative only — derive your own):

> *Need: a quiet wilderness river establishing shot.*
> **Hard:** Stock Footage · Horizontal · No People · delivery resolution
> **Preferred:** Nature/Wilderness · matching season · longer duration · Day
> **Unset:** Location · Events · Date added

**Leaving a filter unset is a decision, not an oversight.** Over-filtering is
the most common failure: it returns twelve near-identical clips from one
contributor and silently destroys diversity.

### Step 4: Apply the filter priority

1. **Content fit** — is it the right subject and action?
2. **Technical fit** — resolution, frame rate, duration, orientation
3. **Pipeline/format fit** — does it suit this production grammar?
4. **Channel/brand fit** — does it suit *this* channel?
5. **Editorial variety** — does it add something the pool lacks?
6. **Optional recency/preference**

When results become too narrow, **relax the softest constraint first** and
record which one you relaxed. Never relax a HARD constraint; if that is the
only way forward, the requirement itself is wrong — say so.

### Step 5 — PASS 1: fast discovery

Screen cheaply and reject fast. Use only what the results grid gives you:
thumbnail · visible metadata · duration · resolution · frame rate · tags.

**Do not open every item page.** Aim to discard obvious mismatches here and
carry perhaps 2–3× the needed count into Pass 2.

### Step 6 — PASS 2: creative confirmation

For survivors, open the item and judge it properly:

- preview playback or keyframes
- composition, subject, lighting
- **camera behaviour** — the thing a thumbnail cannot show
- technical quality, duration, resolution
- distractions: logos, people, man-made intrusions, watermarks
- genuine fit with the requirement

> **For motion-sensitive footage a static thumbnail is not sufficient.** A
> still frame cannot reveal a whip pan, a speed ramp, a zoom, or a shake.
> Inspect motion wherever the provider exposes it.

**Honesty rule.** Record what you actually did. If the session could not play a
preview, the entry says `UNAVAILABLE` — never `PASS`. **Never claim "preview
inspected" for something you could not inspect.** State access limitations
plainly; a caveated list is useful, a falsely confident one is not.

### Step 7: Exact-item verification

The employee must **never make a creative choice**. Search-result URLs are fine
*internally*; they are **not acceptable in the final list**.

Every final entry points to **one exact item**. For each:

1. Open the URL.
2. Confirm it resolves to a single asset, not a search or category page.
3. Confirm the title matches what you inspected.
4. Confirm the preview is the asset you mean.
5. Capture whatever real metadata the page exposes.
6. **Never fabricate a slug or ID.** A plausible-looking URL that 404s costs
   the employee time and costs you their trust in the whole list.

If the provider blocks item or preview inspection without authentication,
**stop and report the login requirement**. Do not fall back to asking the
employee to search and choose.

### Step 8: Cross-list diversity review

Curating items one at a time produces a pool that is individually fine and
collectively monotonous. **Review the whole selected set before shipping it.**

Check for: exact duplicates · near duplicates · the same contributor or shoot
recurring · the same location recurring · too many aerials · too many
close-ups · identical camera motion · identical lighting · redundant
compositions.

**Diversity targets come from the pipeline's creative needs**, not from here.
This skill does not define a shot mix — it only insists that someone checks.

### Step 9: Audio

The same process applies to music, ambience and SFX. Where the provider
permits: audition the preview · check real duration · read metadata and mood ·
verify the exact item page.

Long-form ambience usually wants useful duration, but **the actual duration and
layer requirements come from the pipeline and project**, not from this skill.

Vary the sources deliberately: several genuinely different recordings beat one
file used repeatedly. That is a Director-level requirement to specify and a
procurement-level requirement to satisfy.

### Step 10: Self-evaluate

| Criterion | 1 | 3 | 5 |
|---|---|---|---|
| Requirement traceability | Invented | Loosely from proposal | Every item maps to a stated requirement |
| Filter reasoning | All filters set blindly | Some reasoning | Hard/preferred/unset derived and relaxations recorded |
| Inspection honesty | Claims unverified | Mixed | Every check reflects what was actually done |
| Exact-item discipline | Search URLs shipped | Some exact | Every entry one verified item page |
| Pool diversity | Never reviewed | Spot-checked | Whole pool reviewed against the format's needs |
| Employee clarity | Requires judgement | Mostly mechanical | Click, download, file — no decisions |

Below 4 on any row, fix it before the gate.

### Step 11: Submit — the human gate

Write the employee-facing list (below), then write the checkpoint with
`status="awaiting_human"` and the structured request list in `metadata`, and
**end the turn**. The Markdown file is an operator *view*; the checkpoint is the
state.

**Never auto-download.** Licensing and downloading are the human's action,
deliberately.

---

## Employee-facing `ASSET_LIST.md` standard

Keep the internal complexity — filters, relaxations, rejected candidates —
**out** of this file. The employee sees only what to fetch and where to put it.

```
# EMPLOYEE INSTRUCTIONS

For every item below:

1. Click the exact link.
2. Log into the stock provider if asked.
3. License / download it for this project.
4. Download the quality/version requested.
5. Put the file in the folder shown.
6. Continue to the next item.

If an item is unavailable, note its number and move on.
DO NOT choose a substitute — the AI selects replacements.
```

Then one block per item:

```
001

TYPE:
Video

TITLE:
[actual item title]

SOURCE:
Envato

LINK:
[verified exact item-page URL]

SECTION:
[project-specific]

PURPOSE:
[project-specific]

AI CHECK:
Thumbnail:       PASS
Motion Preview:  PASS / NOT REQUIRED / UNAVAILABLE
Metadata:        PASS
Brand Fit:       PASS

DURATION:
[when known]

RESOLUTION:
[when known]

FRAME RATE:
[when relevant/known]

PUT IN:
[exact local folder]
```

Use absolute paths for `PUT IN` so nobody has to work out the layout.

---

## Provider profile — Envato Elements

*Current provider. Re-check these facts each run; providers change.*

**Search URL:** `https://elements.envato.com/stock-video/<hyphenated-terms>`
and `https://elements.envato.com/sound-effects/<hyphenated-terms>`. These are
**search pages — internal use only**.

**Item URL:** items live at the **root**, not under a category — e.g.
`https://elements.envato.com/<slug>-<ID>`. A link under `/stock-video/…` is a
search page, not an item. This distinction is easy to get wrong and it is the
difference between a usable list and an unusable one.

**Filters observed** (verify at runtime): Category · Orientation · Resolution ·
Frame Rate · Length · Properties (incl. Looped, Alpha Channel) · Date added ·
Location · People · Setting · Theme · Time of day · Events.

**Item pages are readable without signing in**, and carry a `<video>` preview
element — so real duration and real motion can both be checked. Extract
duration from the video element rather than trusting the grid.

**Downloads require an account.** Never attempt an authenticated or bulk
download. If inspection itself starts requiring a login, stop and report it.
