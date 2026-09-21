# Relaxation — Review Director (`review` stage)

Produces: `review`

This gate exists to stop the system shipping two hours of mechanically
concatenated stock footage. Run it on `edit_decisions` **before** rendering,
while fixing things is still cheap.

The purpose is editorial quality. It is **not** to evade monetisation or
duplicate-content detection — and you should not describe it that way. If the
edit is genuinely original and well made, that follows on its own.

Be adversarial. A review that passes everything on the first attempt has
usually not been run properly.

## Measure first, judge second

Compute these from `edit_decisions` and put the numbers in the review. Do not
give an opinion without the evidence next to it.

1. **Shot-duration distribution** — every cut's duration, plus min, max, mean,
   median and standard deviation. Then look for *structure*: a repeating cycle
   (12, 8, 12, 8, …) fails even with a healthy spread. Check for runs of three
   or more near-identical holds (within 10% of each other).

2. **Transition sequence** — the ordered list of transitions. Look for any fixed
   repeating pattern, and for a single transition used on more than ~70% of
   boundaries *where the material did not motivate it*. Straight cuts dominating
   is fine and expected; identical dissolves every 12 seconds is not.

3. **Subject and scale sequence** — the ordered list of (subject, shot scale)
   pairs. Flag adjacent cuts sharing both. Flag any mechanical rotation.

4. **Asset re-use map** — for each asset, where it appears. Flag any asset twice
   in one movement, and any re-use closer than the spacing the scene plan
   promised.

5. **Soundscape evolution** — layer changes over time. Flag any single bed
   running unchanged for more than about 20 minutes, and any layer entering or
   leaving without a fade.

6. **Layer usage** — where multiple audio layers are active. Flag both extremes:
   every layer everywhere (dense and fatiguing) and a single bed throughout
   (flat).

## Then answer these honestly

- Does this read as a mechanical template?
- Are shot lengths obviously repeating?
- Are transitions mechanically repeating?
- Is this just concatenated stock footage?
- Is there an intentional visual progression across movements?
- Does the soundscape evolve, or does it loop?
- Are there real editorial decisions here — do the `reason` fields describe
  choices, or restate what the cut is?
- Is multilayer technique used where it genuinely helps, or everywhere, or
  nowhere?
- Does this feel independently directed?

Read the `reason` field on every cut. Reasons like "next clip" or "river shot"
are a sign nothing was decided. That is a finding.

## Verdict

Each check gets an explicit **pass** or **fail** with its evidence.

**Any fail sends the run back to the `edit` stage before rendering.** Do not
pass something marginal because a re-edit is inconvenient — the render is the
expensive step, not the edit.

When you send back, say specifically what to change: which cuts, which
transitions, which movement. "Add more variety" is not actionable.

If a finding is caused by the footage pool rather than the edit — genuinely not
enough usable material for the target duration — say that plainly and recommend
a shorter target or more footage. Do not send the edit director in circles
trying to fix a problem that cannot be fixed at the timeline level.

## Output

Schema-valid `review` artifact with every check, its measurements, its verdict,
and specific remediation for each fail. Stop for operator approval.
