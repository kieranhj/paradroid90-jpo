# The "TW" Amiga music format (Tony Williams / Nitro, 1990)

Reverse-engineered from `input/extracted/Nitro/tw.Nitro`, the only file in
`Nitro.lha`.  Everything here was recovered by disassembling the embedded 68k
driver; there is no published specification.  All addresses are **file
offsets**, which are also load-relative addresses — the file is one
position-independent blob.

Reproduce the disassembly with:

```
python tools/m68k.py input/extracted/Nitro/tw.Nitro 0x1a 0x7ba
python tools/m68k.py input/extracted/Nitro/tw.Nitro 0x932 0x9d2
python tools/m68k.py input/extracted/Nitro/tw.Nitro 0x1576 0x15e6
```

and the decoded data with `python tools/twdump.py <file> --patterns`.

## Container

There isn't one.  Unlike the DeliTracker *Custom* wrapper around Paradroid
90's JPO tune (an AmigaDOS hunk executable with relocations), `tw.Nitro` is a
**raw 92 186-byte image** with no header, no hunks and no relocation table.
It is fully position-independent: every internal reference is either
PC-relative or relative to `a5`, and `a5` is loaded once with
`lea $4(pc),a5` at the top of the frame routine.

The entry point is offset 0, a `bra.w` to the frame routine.  **Calling
offset 0 once per vertical blank is the entire interface.**  A song is
started by poking its number (1–6) into the request byte and calling; there
is no init call.

One consequence matters when reading the tables: **`a5` is the load address
plus 4**, so every `a5`-relative offset in the data (sample pointers, SFX
script pointers) needs +4 to become a file offset, while every PC-relative
one (period table, instrument table, song table, patterns) does not.  That
four-byte skew is why the last instrument's data ends at `$16816` and the
file ends at `$1681a`.

## Memory map

| Range | Contents |
|---|---|
| `$000`–`$003` | `bra.w` to the frame routine, and also the driver's first four state bytes |
| `$004`–`$019` | 22 bytes of global state (see below) |
| `$01a`–`$09b` | level-4 audio interrupt handler |
| `$09c`–`$111` | per-frame entry point |
| `$112`–`$12f` | volume table, 15 words |
| `$130`–`$7b9` | voice update, sequencer, command handlers |
| `$2e4`–`$30d` | `$80`–`$94` command jump table, 21 words |
| `$30e`–`$31f` | arpeggio table index, 9 words |
| `$320`–`$35d` | arpeggio table data |
| `$64e`–`$693` | period table, 36 words |
| `$71e`–`$7b9` | instrument table, 13 × 12 bytes |
| `$7ba`–`$931` | five voice contexts of `$48` bytes, with `"(c) 1990 Tiny"` wedged between the third and fourth |
| `$932`–`$9d1` | song start |
| `$9d2`–`$a0d` | song table, 6 × 10 bytes |
| `$a0e`–`$16d3` | track lists, pattern byte streams, SFX scripts, SFX index |
| `$16d4`–`$16fb` | 40 zero bytes: the loop target every one-shot falls into |
| `$16fc`–`$1681a` | 90 KB of 8-bit signed PCM |

`$9d2` is the base for **all** 16-bit data offsets: song track-list pointers,
track-list pattern pointers and the `$82` jump target are all
`$9d2 + word`.

## Global state (`a5`, i.e. file offset +4)

| a5 | Meaning |
|---|---|
| `+0` | default song to (re)start; copied into the request byte when nothing is playing |
| `+1` | song currently playing, 0 = stopped |
| `+2` | sound-effect request |
| `+3` | sound-effect priority, 0 = no effect running |
| `+4` | fade speed, 0 = no fade |
| `+5` | fade counter |
| `+6` | master volume, word, `$10` = full |
| `+c` | DMACON word to write at the start of the next frame |
| `+e` | INTENA word to write at the start of the next frame |
| `+10` | pulse-width position (written but never read — see *Dead code*) |
| `+12` | tempo: video frames per sequencer row |
| `+13` | tempo countdown |
| `+14` | song request, 1–6 |

## Per-frame flow (`$09c`)

1. Write the pending `DMACON` from `+c` and the pending `INTENA` from `+e`,
   then clear both.  This is the second half of a note-on: DMA was switched
   *off* last frame, and is switched *on* now.
2. `$932` — if a song has been requested, start it.
3. `$1576` — if a sound effect has been requested and outranks the one
   running, start it.
4. `$130` — update the sound-effect voice, which runs at the full 50 Hz and
   steals channel 3.
5. Decrement the tempo counter, then update voices 0–3.
6. `$61e` — master fade.
7. Reload the tempo counter if it reached zero.

So the sequencer advances one row every `tempo` frames — 50/`tempo` rows per
second — while the envelopes, vibrato, portamento and arpeggios all run
every frame at **50 Hz**.

## Voice context (`$48` bytes)

| Offset | Field |
|---|---|
| `+00` | sample pointer written to `AUDxLC` |
| `+04` | sample length in words, `AUDxLEN` |
| `+06` | current period |
| `+08` | volume, word (`+09` is the live 0–15 level) |
| `+0a` | address of this voice's Paula channel |
| `+0e` | track-list pointer |
| `+12` | pattern pointer |
| `+16` | flags: b0 arpeggio, b1 portamento, b2 bend-on-note, b3 vibrato direction, b4 legato, b5 portamento-on-release, b6 unused |
| `+17` | rows left on the current note |
| `+18` | current note |
| `+19` | semitones added to the note every row |
| `+1a` | loop pointer, used by the audio interrupt |
| `+20` | loop length in words |
| `+22` | this channel's DMACON bit, with bit 15 set |
| `+24` | volume the next note starts at |
| `+25`/`+26` | attack step / number of attack steps |
| `+27`/`+28` | decay step / number of decay steps |
| `+29` | release step |
| `+2a` | row within the note at which release starts |
| `+2b` | release running |
| `+2c`/`+2d` | attack / decay counters |
| `+2e`/`+2f` | vibrato depth / half-period in frames |
| `+30` | vibrato counter |
| `+31` | portamento frames left |
| `+32` | portamento step, word, bit 15 = subtract |
| `+34` | bend added to the period at note-on, word |
| `+36` | portamento length loaded at note-on |
| `+37` | default note length in rows |
| `+38` | this channel's INTENA bit |
| `+3a`/`+3b`, `+3c`/`+3d`, `+3e`/`+3f` | attack / decay / release rate counter and reload |
| `+40` | vibrato delay in rows |
| `+41`/`+42` | arpeggio index / arpeggio table pointer |
| `+46` | transpose |

There are five of these: four for the music and a fifth at `$8ea` for the
sound effect, which shares Paula channel 3.

## Song table (`$9d2`, 6 × 10 bytes)

Four words — a track-list offset per voice — then a tempo byte and a pad.
Only six entries; there is no count field, the number is fixed in the code.

| Song | Tempo | Rows/s | Length | Loop |
|---|---|---|---|---|
| 1 | 5 | 10.0 | 1216 rows (121.6 s) | row 320 |
| 2 | 4 | 12.5 | 329 rows | ends |
| 3 | 4 | 12.5 | 77 rows | ends |
| 4 | 4 | 12.5 | 49 rows | ends |
| 5 | 5 | 10.0 | 125 rows | ends |
| 6 | 1 | 50.0 | 518 rows | row 6 |

A track list is a run of words, each `$9d2 + word` pointing at a pattern,
terminated by `$0000`.  The word *after* the terminator is the track-list
offset to resume from, so a song can loop into the middle of its own order
list.  Songs 3, 4 and 5 nest their voices' lists — voice 1's list is voice
0's list advanced by one entry — and end by hitting a `$83` in the pattern
rather than by running off the list.

## Pattern byte stream

One byte stream per voice, read until something ends the row.  All state
(instrument, volume, note length, envelope, vibrato) persists across pattern
boundaries.

| Byte | Meaning |
|---|---|
| `$00`–`$7f` | play note *n* (index into the period table); ends the row |
| `$80` | end of pattern — fetch the next one from the track list |
| `$81` | slide the note down one semitone per row |
| `$82` *w* | set the track pointer to `$9d2 + w` and continue there |
| `$83` | stop the song |
| `$84` *r* *sn* | release at row *r*, subtract *n* every *s* frames |
| `$85` | rest for the current note length; ends the row |
| `$86` | volume to 0, then rest; ends the row |
| `$87` *t* | transpose (signed) |
| `$88` *d* *p* *s* | vibrato: delay *d* rows, depth *p*, half-period *s* frames |
| `$89` *lo hi lo hi n* | add a bend to the period at note-on, then portamento |
| `$8a` | bend off |
| `$8b` *lo* *hi* *n* | portamento: add the signed-magnitude word to the period for *n* frames |
| `$8c` *lo* *hi* *n* | the same, but armed at release instead of at note-on |
| `$8d` | portamento-on-release off |
| `$8e` / `$8f` | legato on / off (a note does not retrigger the sample) |
| `$90` | arpeggio off |
| `$91` / `$92` | note volume −1 / +1 |
| `$93` | start the master fade |
| `$94` | end of a sound effect |
| `$a0`–`$af` | select arpeggio table 0–15 and enable it |
| `$b0`–`$bf` | note volume 0–15 |
| `$c0`–`$cf` *ad* *ds* *rr* | note volume 0–15 plus the envelope: attack step/count, decay step/count, attack rate/decay rate, one nibble each |
| `$d0`–`$df` | select instrument 0–12 |
| `$e0`–`$ff` | note length 1–32 rows |

Nine arpeggio tables exist (`$a0`–`$a8`); the index table only has nine
entries even though the opcode space allows sixteen.  Each is a run of
semitone offsets, one per frame, with bit 7 marking the last entry, after
which the index wraps to 0.  The tune uses tables 1, 2, 3 and 5.

## Instruments (`$71e`, 13 × 12 bytes)

| Offset | Field |
|---|---|
| `+0` | sample offset, `a5`-relative (add 4 for the file offset) |
| `+4` | length in words |
| `+6` | loop offset, `a5`-relative |
| `+a` | loop length in words |

That is the whole instrument.  There is no volume, no finetune and no
envelope: everything expressive lives in the pattern stream, which is the
exact opposite of JPO's design (see [`COMPARISON.md`](COMPARISON.md)).

Twelve of the thirteen instruments loop into the 32 zero bytes at `$16d4`,
which is the driver's idiom for "one-shot": the audio interrupt at `$01a`
repoints `AUDxLC`/`AUDxLEN` at the loop the moment the sample runs out, so
the channel falls silent rather than repeating.  Only instrument 4 has a
real loop, 3 974 bytes at the end of its own 16 KB.

| # | Bytes | Cycle | Role |
|---|---|---|---|
| 1 | 1 456 | — | percussion |
| 2 | 3 082 | — | percussion |
| 3 | 4 224 | 127 | tonal |
| 4 | 16 332 | 251 | tonal, the only looped instrument |
| 5 | 3 464 | — | percussion |
| 6 | 15 008 | — | percussion |
| 7 | 12 322 | 127 | tonal, the main lead |
| 8–12 | 6–8 KB each | — | used only by the sound effects |
| 10 | 4 108 | 95 | tonal |

*Cycle* is the autocorrelation period measured by `tools/twtovgm.py`; the
driver itself has no idea, which is why the SN76489 conversion has to
measure it.

## Pitch and volume

The period table at `$64e` is **exactly the ProTracker period table**: 36
entries, 856 down to 113, three octaves at finetune 0.  Notes index it
directly after adding the transpose, and vibrato, portamento and bend then
add signed offsets to the looked-up period.  There are no periods outside
that range anywhere in the data, which is the single biggest reason the MOD
conversion comes out clean.

Volume is a 0–15 level looked up in a 15-entry table at `$112`
(`0 2 3 4 6 8 10 13 17 22 28 35 40 51 64`) after being scaled by the master
volume — `(vol * master) >> 4`, master `$10`.  The table has **fifteen**
entries and the index can reach 15, so a note at full volume reads the first
word of the code that follows (`$4a2d`), and Paula takes the low seven bits
of it: 45 instead of 64.  Level 15 is quieter than level 14.  The replayer
reproduces this rather than clamping, because the data does use level 15.

## Note-on

Triggering a note takes two frames:

1. Write `AUDxLC`/`AUDxLEN`, turn the channel's DMA **off** and its audio
   interrupt off, and latch the enable words into `+c`/`+e`.
2. Next frame, the frame routine writes those words, turning DMA and the
   interrupt on.

The envelope, volume and vibrato counters are reset in step 1, and the
period and volume registers are written at the top of every frame from the
values computed on the previous one — so there is a one-frame (20 ms) lag
between the sequencer deciding something and Paula hearing it.

## Sound effects

`$1576` implements a priority-based effect channel.  A request byte
(`a5+2`, 1–11) is compared against the running effect's number; a higher one
wins, and the effect takes over Paula channel 3 through the fifth voice
context at `$8ea`, which is updated every frame instead of every row.  The
effect scripts are ordinary pattern byte streams — the same opcodes — indexed
by a table of `a5`-relative offsets at `$15e6` and terminated by `$94`.
Instruments 8 to 12 exist only for them.

The first script, for example, is
`c8 16 1f 24 | 84 01 11 | d8 | ff | 0c | 94`: volume 8 with a 6-step attack
and a 15-step decay, release on row 1, instrument 8, 32 rows long, note 12,
end.

## Dead code

`$494` builds a pulse wave of varying duty in the `$16d4` buffer, one byte
per call, and would have given instrument 0 a PWM lead.  Nothing calls it.
The note-on handler still resets its position (`a5+10 = $20`) whenever a
voice selects instrument 0, and the buffer is still the loop target for
every one-shot, but since nothing ever writes to it, it stays 32 zero bytes
and instrument 0 is silence.  The replayer omits the routine for the same
reason the driver never runs it.
