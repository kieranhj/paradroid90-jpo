# Amiga game music: unpack, reverse-engineer, replay, convert

Two undocumented Amiga music drivers, each taken apart from its 68k code,
re-implemented in Python, and converted to ProTracker MOD and to SN76489
register logs for the BBC Micro.

* **Paradroid 90** — Jason Page's "JPO" driver, 1990, Hewson / Graftgold.
  `input/Paradroid_90.lha`, from Exotica's
  [Paradroid 90](https://www.exotica.org.uk/wiki/Paradroid%2090) page.
* **Nitro** — Tony Williams' "TW" driver, 1990, Psygnosis.
  `input/Nitro.lha`.

They are close to opposite designs. JPO is a parametric synthesiser: 17
single-cycle waveforms, 8 KB of sample data, and every envelope, sweep and
arpeggio inside the instrument record. TW is 90 KB of sampled instruments
with no parameters at all, and every envelope, vibrato and arpeggio typed
into the pattern stream. Same year, same hardware, same four channels.

**[`docs/FORMAT.md`](docs/FORMAT.md)** and
**[`docs/TW_FORMAT.md`](docs/TW_FORMAT.md)** are the reverse-engineered
format specifications, and **[`docs/COMPARISON.md`](docs/COMPARISON.md)** is
a technical assessment of JPO against ProTracker — what each format buys and
what it costs, with the measurements behind it. `docs/module_dump.txt` and
`docs/nitro_dump.txt` are full decoded dumps of the two tunes;
`docs/dis_main.txt` and `docs/nitro_dis.txt` are the 68k disassemblies.

## Layout

```
input/       the two .lha archives and their extracted contents
tools/       the replayers, the analysis tools and the converters
output/mod/  ProTracker conversions
output/vgm/  SN76489 / BBC Micro conversions
output/wav/  Amiga renders and BBC previews -- not checked in, see the
             README there for the one-liners that regenerate them
docs/        format specs, module dumps, disassemblies
```

Paradroid 90 is documented first; [Nitro](#nitro-the-tw-format) follows.

# Paradroid 90: the JPO format

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
python tools/stats.py $MOD                  # the figures quoted in COMPARISON.md
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


# Nitro: the TW format

`input/Nitro.lha` holds one file, `tw.Nitro`, the music from Psygnosis'
*Nitro* (1990) as ripped for UADE. Music and sound by **Tony Williams**; the
driver signs itself `(c) 1990 Tiny` in the middle of its voice contexts.

```
7z x input/Nitro.lha -oinput/extracted
```

Throughout this section, `$TW` means `input/extracted/Nitro/tw.Nitro`.

## What the format is

Not a container at all. `tw.Nitro` is a **raw 90 KB position-independent 68k
blob** — no hunk header, no relocations, no magic — holding the driver, its
tables, the sequence data and the samples. The whole interface is *call
offset 0 once per vertical blank*; a song is started by poking its number
into a state byte first. [`docs/TW_FORMAT.md`](docs/TW_FORMAT.md) has the
full map.

The sequencer advances one row every `tempo` frames (10 to 12.5 rows/s here)
while the modulation — a four-parameter volume envelope with a separate
release stage, vibrato with a delay, portamento that can be armed at note
release, and nine arpeggio tables — runs every frame at 50 Hz. Instruments
are just `(sample, length, loop, loop length)`: thirteen 8-bit PCM sounds,
twelve of them one-shots that loop into 32 zero bytes when they run out, and
one 16 KB looped bass. Six subsongs: a 2:02 main theme, a 26 s tune, three
short jingles and a 10 s effect bed.

Two details worth calling out. The period table is **exactly the ProTracker
table** — 36 entries, 856 down to 113 — which is most of why the MOD
conversion comes out clean. And the volume table has fifteen entries while
the index can reach fifteen, so a note at full volume reads the first word
of the code that follows it and comes out *quieter* than level 14; the
replayer reproduces that rather than clamping, because the tune does use
level 15.

## Playing it

```
python tools/twrender.py $TW -s 1 -o output/wav/nitro_sub1.wav
python tools/twrender.py $TW -s 1 --play           # render then play (Windows)
python tools/twrender.py $TW -s 1 --trace          # print every note event
```

`tools/tw.py` is the replayer, a routine-for-routine reimplementation of the
68k driver sharing the Paula emulation in `tools/jpo.py`. Song length comes
from detecting when the sequencer state repeats, so subsong 1 renders its
full 121.6 s period rather than stopping when one voice happens to wrap.

## Inspecting it

```
python tools/twdump.py $TW --patterns          # songs, instruments, patterns
python tools/m68k.py $TW 0x1a 0x7ba            # disassemble the driver
```

## Converting

### ProTracker MOD

```
python tools/twtomod.py $TW -s 1 -o output/mod/nitro_sub1.mod
```

This one is a near-perfect structural fit. Both formats run off 20 ms
frames, so setting the MOD speed to the driver's tempo makes **one driver
row exactly one MOD row with zero drift**; the period table is ProTracker's,
so notes are bit-exact and nothing falls outside 113..856; the instruments
are ordinary 8-bit one-shots, so they are copied byte-for-byte, with the
one looped instrument keeping its loop and the rest given two bytes of
silence to loop in (which is what the driver does).

What has to be approximated is the 50 Hz modulation against ProTracker's one
effect per channel per row. Effects are chosen per cell in this order: `Cxx`
on a note trigger, `0xy` while an arpeggio table runs, `1xx`/`2xx` while a
portamento runs, `4xy` for vibrato (and `6xy` after the first vibrato row, so
a decaying note can vibrate *and* track its envelope), and `Cxx` otherwise.

Verified against the Amiga render by rendering the MOD through VLC:
**0.92 correlation** between the two amplitude envelopes at row resolution,
and 816 of 963 detected note onsets matching with a mean offset of
—5 ms. The residual is entirely sub-row: at 10 ms resolution the
correlation drops to 0.71, which is the ADSR detail that ProTracker
structurally cannot hold.

*Dropped in translation:* intra-row envelope shape, the exact arpeggio
sequences (tables of up to nine offsets become MOD's three-step `0xy`), and
volume changes on rows where a pitch effect wins the column.

### BBC Micro (SN76489)

```
python tools/twtovgm.py $TW -s 1 -v -o output/vgm/nitro_sub1.vgm
python tools/vgmrender.py output/vgm/nitro_sub1.vgm \
       -o output/wav/nitro_sn_sub1.wav          # hear it without a BBC
```

VGM 1.50 at 50 Hz — the driver's own frame rate, so the log is
frame-for-frame with the Amiga — with the SN76489 clock set to the BBC's
4 MHz. `--rate 100` for a 100 Hz player.

Going from four channels of PCM to three squares and a noise channel needs
three decisions, all made automatically, all reported, all overridable:

* **Which instruments are pitched.** Nothing in the data says. Each sample
  is autocorrelated: strong repetition means a fundamental and a tone
  channel, weak means percussion and the noise channel. That splits Nitro's
  instruments cleanly, and voice 2 — which only ever plays the two drum
  sounds — ends up entirely on noise, leaving all three tone channels for
  the three melodic voices. Nothing is dropped.
* **Pitch.** A sampled instrument's real pitch is
  `(3546895 / period) / cycle_length`, and the cycle length differs per
  instrument: 251 samples for the bass, 127 for the lead, 95 for the third
  voice. Each tone channel then gets one constant octave shift taken from
  the low end of its own range, so nothing hits the BBC's 122 Hz floor
  (0 frames clipped in subsong 1) and the octave jumps survive.
  `--transpose` shifts everything on top.
* **Percussion timbre.** Each drum's noise shift rate comes from the
  zero-crossing rate of its sample, putting the kick on 122 Hz and the
  snare on 488 Hz.

Verified by rendering the VGM through the SN76489 emulator in
`tools/vgmrender.py` and measuring: the intended pitch is the strongest of
its five nearest semitones in **100 of 103** probes across the tune.

# Tools

| | |
|---|---|
| `tools/hunk.py` | AmigaDOS hunk parser + relocator |
| `tools/m68k.py` | 68k disassembler with resync (capstone) |
| `tools/jpo.py` | the replayer + Paula emulation |
| `tools/render.py` | subsong → WAV |
| `tools/dump.py` | songs / instruments / patterns dump |
| `tools/stats.py` | data-footprint measurements (JPO vs the converted MOD) |
| `tools/tomod.py` | subsong → ProTracker MOD |
| `tools/tovgm.py` | subsong → SN76489 VGM (BBC Micro) |
| `tools/vgmrender.py` | VGM → WAV through an SN76489 emulation |
| | *and for the TW format:* |
| `tools/tw.py` | the TW replayer + Paula emulation |
| `tools/twrender.py` | TW subsong → WAV |
| `tools/twdump.py` | TW songs / instruments / patterns dump |
| `tools/twtomod.py` | TW subsong → ProTracker MOD |
| `tools/twtovgm.py` | TW subsong → SN76489 VGM (BBC Micro) |

Requires Python 3. `capstone` is needed only by `m68k.py`.

## Credits and provenance

The Paradroid 90 archive came from Exotica's
[Paradroid 90](https://www.exotica.org.uk/wiki/Paradroid%2090) page; there is
more about the game itself on
[Lemon Amiga](https://www.lemonamiga.com/game/paradroid-90).

*Paradroid 90* © 1990 Graftgold / Hewson — music and sound by
**Jason Page**, programmed by Andrew Braybrook, O.O.P.S. kernel by Dominic
Robinson, graphics by Michael A. Field, John Cumming and John W. Lilley. The
archive's `Custom_Version` DeliTracker wrapper is by whoever ripped it.

*Nitro* © 1990 Psygnosis — music and sound by **Tony Williams**, whose
driver signs itself `(c) 1990 Tiny`. `tw.Nitro` is a UADE-format rip.

Everything in `tools/` and `docs/` here is new work; the music data and
samples in `input/` are not.
