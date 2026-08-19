# Paradroid 90 — Amiga "JPO" music: unpack, replay, convert

`input/Paradroid_90.lha`, from Exotica's
[Paradroid 90](https://www.exotica.org.uk/wiki/Paradroid%2090) page, holds
Jason Page's 1990 Amiga soundtrack for Hewson / Graftgold's *Paradroid 90*.
This repo unpacks it, documents the undocumented
replay format, plays it back accurately, and converts it to ProTracker MOD and
to SN76489 register logs for the BBC Micro.

**[`docs/FORMAT.md`](docs/FORMAT.md)** is the reverse-engineered format
specification. `docs/module_dump.txt` is a full decoded dump of this tune and
`docs/dis_main.txt` is the annotated 68k disassembly of the driver.

## Layout

```
input/       Paradroid_90.lha and its extracted contents
tools/       the replayer, the analysis tools and the two converters
output/mod/  ProTracker conversions
output/vgm/  SN76489 / BBC Micro conversions
output/wav/  Amiga renders and BBC previews -- not checked in, see the
             README there for the one-liners that regenerate them
docs/        format spec, module dump, disassembly
```

## Unpacking

`-lh5-` LHA. 7-Zip handles it directly:

```
7z x input/Paradroid_90.lha -oinput/extracted
```

Three files come out; `Custom_Version/cust.Paradroid_90` is the DeliTracker
Custom module — an Amiga hunk executable carrying both the 68k replay routine
and the music data. That is the one everything here works from. (The two
`jpo.*` files are UADE-format rips of the same tune with no player code; the
`_1Mb` one carries the full-size sample set.)

Throughout, `$MOD` means
`input/extracted/Paradroid_90/Custom_Version/cust.Paradroid_90`.

## What the format is

A ~100 Hz (99.856 Hz, CIA-timed) sequencer driving Paula directly. Four
voices, each a byte-code stream of notes, durations, instrument selects and
portamento commands. Instruments are 48-byte records holding a waveform index,
a four-stage volume envelope with a tremolo-capable sustain, a nested
pitch-envelope/arpeggio machine, and vibrato. Waveforms are seventeen
single-cycle shapes plus four sampled drums, and one instrument class plays
white noise by jittering `AUDxLC` through a 1 KB random buffer every frame.
Five subsongs: a 2:34 title theme, a 54 s in-game tune, and three short
jingles.

## Playing it

```
python tools/render.py $MOD -s 1 --loops 0 -o output/wav/out_sub1.wav
python tools/render.py $MOD -s 1 --play          # render then play (Windows)
python tools/render.py $MOD -s 1 --trace         # print every note event
```

`tools/jpo.py` is the replayer: a routine-for-routine reimplementation of the
68k driver plus a Paula emulation (correct DMA restart semantics, one-shot to
loop transitions, box-filtered resampling). `tools/render.py` writes a stereo
WAV.

Useful flags: `--master` (see *Volume* below), `--stereo` (Amiga LRRL
separation), `--loops`.

## Inspecting it

```
python tools/dump.py $MOD --patterns        # songs, instruments, decoded patterns
python tools/hunk.py $MOD flat.bin          # flatten + relocate the hunks
python tools/m68k.py flat.bin 0x296 0xa3c   # disassemble the driver
```

## Converting

### ProTracker MOD

```
python tools/tomod.py $MOD -s 1 -o output/mod/paradroid90_sub1.mod
```

One JPO song tick maps onto one MOD row: the driver's tick is 0.1202 s and a
default ProTracker row (speed 6, BPM 125) is 0.1200 s, so the module needs no
tempo commands at all and drifts 0.15 %.

Instruments split two ways. Anything whose pitch holds steady for the whole
note becomes a looped MOD sample built from the raw JPO waveform, with the
*exact* Paula period written into the note column and the volume envelope
written out as `Cxx`. Everything else — the sampled drums, the pitch sweeps
and the fixed-chord arpeggio instruments — is pre-rendered offline with its
envelope and sweep baked in, one one-shot sample per (instrument, note) pair
the song actually uses, so those are reproduced exactly. Instruments whose
notes fall below ProTracker's range get an octave-stretched copy of their
waveform, which keeps every module inside the standard 113–856 period range.

Verified against the Amiga render by rendering the MOD through VLC: note
onsets match to a mean of 10 ms with all periods in range.

*Dropped in translation:* instrument vibrato, sub-quarter-tone pitch wobble on
sustained notes, and volume resolution on those notes (99.86 Hz → 8.33 Hz).

### BBC Micro (SN76489)

```
python tools/tovgm.py $MOD -s 1 -o output/vgm/paradroid90_sub1.vgm
python tools/vgmrender.py output/vgm/paradroid90_sub1.vgm \
       -o output/wav/sn_sub1.wav          # hear it without a BBC
```

Emits VGM 1.50 at 50 Hz with the SN76489 clock set to the BBC's 4 MHz, which
is what the BBC Micro VGM players take, and which VGMPlay / foobar2000 will
play as-is. `--rate 100` for a 100 Hz player.

Three arrangement decisions are made automatically and all are reported and
overridable:

* **Voice → channel.** Instruments built on the sampled drums or on the noise
  waveform are percussion and go to the noise channel; instruments built on a
  single-cycle waveform keep a real pitch and go to a tone channel. The bass
  voice, which also fires the snare, therefore moves between its tone channel
  and the noise channel exactly as the arrangement intends. The three busiest
  tone voices take channels 0–2; anything beyond that is dropped **and printed**
  (only subsong 3 loses a part). `--tone-voices` overrides the choice.
* **Pitch.** A single-cycle waveform's real pitch is
  `(3546895 / period) / cycle_length`, not the Paula rate. The BBC floor is
  122 Hz and this tune's bass sits at 58 Hz, so each tone channel gets one
  constant octave shift taken from the low end of its own range — folding note
  by note instead would collapse the riff's C-1/C-2 octave jumps onto the same
  pitch. Subsong 1 ends up with the bass +2 octaves and the lead +1.
  `--transpose` shifts everything on top of that.
* **Percussion timbre.** Each drum's noise shift rate comes from the
  zero-crossing rate of its rendered waveform, so the kick lands on the 122 Hz
  rate and the snare and hi-hat on 488 Hz.

Verified by rendering the VGM through an SN76489 emulator and measuring the
result: 232 Hz / 466 Hz / 659 Hz where the driver calls for 232 / 466 / 660,
with note onsets aligned to the Amiga render.

## Volume — one judgement call

The driver keeps a 9-bit volume and writes `vol >> 3` to `AUDxVOL`, and
DeliTracker hands it `SndVol * 4 - 1`. With DeliTracker's documented 0–64
slider that is a master of **255**, so the module tops out at Paula volume 31
— half of the hardware range. Two instrument envelopes peak at exactly 252 and
254, which is hard to read as anything but 255 being the intended full scale,
so that is the default. `--master 511` uses the full 0–63 range instead; it
does not just make things louder, it changes the balance between the
instruments that clamp at the master (bass, lead) and those that do not
(drums). The MOD and VGM converters apply a compensating 2× gain so the
converted files are not quiet.

## Tools

| | |
|---|---|
| `tools/hunk.py` | AmigaDOS hunk parser + relocator |
| `tools/m68k.py` | 68k disassembler with resync (capstone) |
| `tools/jpo.py` | the replayer + Paula emulation |
| `tools/render.py` | subsong → WAV |
| `tools/dump.py` | songs / instruments / patterns dump |
| `tools/tomod.py` | subsong → ProTracker MOD |
| `tools/tovgm.py` | subsong → SN76489 VGM (BBC Micro) |
| `tools/vgmrender.py` | VGM → WAV through an SN76489 emulation |

Requires Python 3. `capstone` is needed only by `m68k.py`.

## Credits and provenance

The archive came from Exotica's
[Paradroid 90](https://www.exotica.org.uk/wiki/Paradroid%2090) page. There is
more about the game itself on
[Lemon Amiga](https://www.lemonamiga.com/game/paradroid-90).

Music and sound by **Jason Page**. *Paradroid 90* © 1990 Graftgold / Hewson —
programmed by Andrew Braybrook, O.O.P.S. kernel by Dominic Robinson, graphics
by Michael A. Field, John Cumming and John W. Lilley. The archive's
`Custom_Version` DeliTracker wrapper is by whoever ripped it. Everything in
`tools/` here is new work; the music data and samples in `input/` are not.
