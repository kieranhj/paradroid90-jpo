# JPO vs ProTracker: a technical assessment

A comparison of Jason Page's "JPO" Amiga music driver (documented in
[`FORMAT.md`](FORMAT.md)) with the ProTracker MOD format, from the point of
view of someone who has just implemented both ends of a converter between
them.

Every number below is measured from `cust.Paradroid_90` and its converted
module, and can be reproduced with:

```
python tools/stats.py input/extracted/Paradroid_90/Custom_Version/cust.Paradroid_90
```

## They aren't the same kind of thing

ProTracker MOD is a **data format** with a de facto reference interpreter.
JPO is a **driver plus its data tables** — the semantics live in 3.6 KB of 68k
code shipped alongside. Almost every difference below follows from that one
distinction: MOD had to standardise on a set of effects general enough for any
tune, while Jason Page could put whatever he wanted in the instrument record
because he also wrote the thing that reads it.

## Density

Measured on subsong 1 — 153.6 s, 3 voices:

| | JPO | MOD (this repo's conversion) |
|---|---|---|
| Sequence data | **676 bytes** | 11 392 bytes |
| …per second | 4.4 B/s | 74.1 B/s |
| Empty cells in the grid | n/a | 57 % |
| Melodic waveform data | 496 bytes (17 single-cycle waves) | same 496 B, if hand-made |
| Sampled drums | 7 620 bytes | same |

17× on the sequence data. Two causes. First, MOD allocates a fixed
64 × 4 × 4-byte grid whether or not anything happens in it — 57 % of the cells
in the conversion are literally zero. JPO is a variable-length byte stream per
voice where a note is *one byte* and its duration is a separate run-length
command that persists until changed. Second, JPO's note lengths are expressed
once (`$80–$AF`, 1–48 ticks) rather than as silence padded out across rows.

The 96 KB of sample data in the converted MOD is a conversion artifact, not a
fair charge against the format — envelopes were baked into one-shots. A
hand-built MOD would use the same ~8 KB of waveform data. But that is exactly
the point of the next section: a hand-built MOD *couldn't* reproduce the tune
from those 8 KB.

## The central tradeoff: where does modulation live?

**JPO puts it in the instrument.** Each 48-byte record carries a four-stage
volume envelope (with a sustain stage that can flip its delta sign for
tremolo), a nested pitch-envelope machine with deltas, delta-acceleration and
loop flags, vibrato depth/speed/delay, and a chain-to-next-instrument field.
All of it runs at **99.856 Hz, unconditionally, on every voice, for free** —
twelve envelope updates per song tick.

**MOD puts it in the pattern.** ProTracker instruments are inert: a sample, a
volume, a loop point, a finetune. Every envelope, sweep, arpeggio and vibrato
has to be typed into the effect column, and there is exactly **one effect per
channel per row**. ProTracker has no volume column — that arrived with
XM/S3M — so volume and pitch modulation compete for the same byte.

That single constraint shaped the converter in
[`tools/tomod.py`](../tools/tomod.py). It can write exact Paula periods into
the note column, or write `Cxx` volumes, but not both on the same row for the
same channel — so 16 of the 18 samples in the converted module are
**pre-rendered one-shots**, with envelope and sweep burned into PCM. That is
not a limitation of the converter; it is the only way MOD reproduces a 100 Hz
parametric sweep. It is also why the file went from 8 KB of waveforms to 96 KB
of audio: the format's parametric compression was traded for raw samples.

Second-order effect: MOD slides only run on ticks 1…speed−1, never tick 0. At
the default speed 6 that is five volume-slide steps per row against JPO's
twelve envelope updates — and only when the effect column is free.

## Pitch

JPO's period table is 84 entries (7 octaves) of `61156 / 2^(n/12)`, and the
value stays at **16-bit precision** through the whole pitch-envelope chain,
right-shifted by a per-instrument amount only at the final write to
`AUDxPER`. With a shift of 6 the internal resolution is 1/64 of a Paula period
unit.

MOD stores a 12-bit period in the pattern cell, with a UI covering three
octaves and per-*sample* finetune in eighths of a semitone. Its smallest
expressible pitch step at period 477 is ~3.6 cents; JPO's is ~0.06 cents.
That is the difference between steppy and smooth vibrato in the low register,
and it is why the bass instrument's ±1-unit wobble reads as warmth rather than
as a bug.

MOD's compensating strength: the period lives in the pattern as a raw number,
so a MOD is not actually restricted to its 36-note grid — which this repo's
converter exploits to get bit-exact pitches for the static instruments.

## Structure and state

JPO gives **each voice its own order list**, read independently with no
barrier between voices. Patterns are therefore free to be different lengths
per voice; genuine polymeter is expressible. MOD has one order list shared by
all four channels and a hard 64-row barrier.

Page didn't use the freedom — every pattern in both songs is exactly 64 ticks,
which is why the conversion lands on a clean 1:1 JPO-pattern → MOD-pattern
mapping. But the format also makes patterns **non-self-contained**: note
duration, selected instrument and portamento speed all persist across pattern
boundaries, which is why several patterns open with rests whose length is
inherited from whatever played before. MOD patterns are self-describing
(effect memory aside), and that is what makes a tracker's copy/paste and
pattern-reordering UI possible at all.

## What JPO has that MOD structurally lacks

* **Instrument priority.** Every instrument carries a priority byte; a trigger
  is ignored unless it outranks what is already sounding on that voice. This
  is the mechanism by which in-game SFX steal channels from the music without
  a separate mixer. A music format has no reason to have this; a *game* driver
  cannot work without it.
* **Subsong chaining.** A `next song` field per subsong, so intros chain into
  loops and the driver handles it. All five music subsongs here loop
  themselves; the sixth table entry is the SFX bank.
* **Instrument chaining.** An instrument can trigger another when its release
  completes — two-stage sounds without touching the pattern.
* **Parametric noise.** A negative waveform index re-points `AUDxLC` into a
  1 KB random buffer every frame, producing continuously varying noise from
  1 KB. In MOD you loop one noise sample and hear the loop.

## Where JPO is genuinely worse

* **Pitch-envelope steps 1+ hold absolute values, not offsets.** Only step 0
  receives the played note, so a transposable arpeggio is impossible. This
  tune pays for it with instruments 6–11: six near-identical 48-byte records
  differing *only* in their two chord pitches, where one transposable
  instrument would have done. That is a real data-model flaw, not a stylistic
  choice.
* **Only 32 instruments are reachable from pattern bytes** (`$D0–$EF` select,
  `$B0–$CF` trigger) even though 106 slots are defined; the rest are
  addressable only through the SFX call interface. The opcode space ran out.
* **Half the volume range is thrown away.** 9-bit volume, `>> 3` to
  `AUDxVOL`, master 255, so the module never exceeds Paula volume 31 of 64.
  Two instrument envelopes peaking at exactly 252 and 254 show the ceiling was
  designed at 255 deliberately, so this is a 6 dB gift to nobody.
* **No random access.** The driver is a set of coroutines with resume-address
  pointers; there is no way to start at bar 33 without running the state
  machine from the top. MOD seeks to any order position instantly.
* **A three-frame note-on latency** — DMA off, DMA on at zero volume, then
  install the loop and start the attack. 30 ms of unavoidable slop before any
  note speaks.
* **No editor.** The data came out of Page's own bespoke tool, now lost. MOD's
  real superpower is that a musician can open it, hear it and change it on any
  machine made since 1987.

## Summary

| | JPO | ProTracker MOD |
|---|---|---|
| Nature | driver + tables | data format |
| Sequence density | 4.4 B/s | 74 B/s |
| Modulation | parametric, in the instrument, 100 Hz | typed per row, one effect per channel |
| Pitch resolution | 16-bit internal, 7 octaves | 12-bit period, 3-octave UI + finetune |
| Voice structure | independent order list per voice | one shared order list, 64-row barrier |
| Pattern state | persists across patterns | self-contained |
| SFX integration | priority system built in | none |
| Random access | none | any order position |
| Authoring | bespoke tool, lost | universal, still maintained |
| Portability | needs the 68k code | plays everywhere |

JPO is the better **game** format and MOD is the better **music** format, and
the numbers say why. JPO buys a 17× sequence-density win and free 100 Hz
parametric modulation by hard-coding its synthesis model into a specific
replay routine, and pays for it with zero portability, no tooling, and a note
range that cannot transpose its own arpeggios. MOD gives up per-frame
expression to become a self-describing file that any machine can play and any
musician can edit — and that trade is the reason we can still open a MOD in
2026 and had to disassemble 3.6 KB of 68k to open this.

The clean tell is the conversion itself. The parts of the tune that map onto
MOD's model — notes, durations, static pitches, slow volume shapes — came
across bit-exact and cost 11 KB. The parts that live in JPO's synthesis model
had to be rendered down to 96 KB of PCM, because MOD has nowhere to put them.
