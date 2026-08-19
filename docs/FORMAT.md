# Paradroid 90 — "JPO" (Jason Page Old) music format

Reverse-engineered from `Paradroid_90.lha` →
`Paradroid_90/Custom_Version/cust.Paradroid_90`.

```
Jason Page | Paradroid 90 | 1990 | Hewson / Classic / Graftgold
$VER: DeliCustom: Paradroid90 - (c) 1990 by Graftgold,
Programmed by Andrew Braybrook, O.O.P.S. Kernel by Dominic Robinson,
Graphics by Michael A. Field, John Cumming and John W. Lilley,
Music and Sound by Jason Page
```

## 1. The archive

`Paradroid_90.lha` is an **LHA `-lh5-`** archive (LZSS + Huffman). 7-Zip reads
it directly:

```
7z x Paradroid_90.lha -oextracted
```

| file | size | what it is |
|---|---|---|
| `Paradroid_90/jpo.Paradroid_90` | 17 213 | UADE/EaglePlayer rip ("Jason Page Old"), small sample set |
| `Paradroid_90/jpo.Paradroid_90_1Mb` | 81 063 | same tune, full 1 MB sample set |
| `Paradroid_90/Custom_Version/cust.Paradroid_90` | 22 052 | DeliTracker **Custom** module: 68k replay code **+** data |

The `jpo.*` files are bare data blobs with a small pointer-patching stub; they
need UADE's Jason Page player. The `cust.*` file carries the original replay
routine with it, which is what this project reverse-engineers.

## 2. The container

`cust.Paradroid_90` is a standard **AmigaDOS hunk executable** (`$000003F3`):

| hunk | type | size | contents |
|---|---|---|---|
| 0 | CODE | `$0e14` | replay routine + DeliTracker glue + period table + player context |
| 1 | DATA (chip) | `$040c` | 2 bytes of silence + a 1 024-byte noise sample |
| 2 | DATA (chip) | `$4310` | instruments, waveforms, songs, sequences |

`tools/hunk.py` parses it and applies HUNK_RELOC32, producing a flat image.
All addresses below are offsets into that flat image.

At offset `$04` sits the DeliTracker `DELIRIUM` tag followed by a TagList:

| tag | offset | routine |
|---|---|---|
| `$80004455` | 1 | DTP_PlayerVersion |
| `$80004456` | `$11` | player name string |
| `$80004473` | `$13e` | longword patched with the DeliTracker globals pointer |
| `$8000445e` | `$142` | **interrupt** — one replay frame |
| `$80004462` | `$158` | subsong range → `1..5` |
| `$80004463` | `$15e` | init sound: install the four root pointers, seed RNG, alloc audio |
| `$80004464` | `$1a0` | end sound |
| `$80004465` | `$1a8` | start: set `dtg_Timer = $1bc0`, start subsong |
| `$80004466` | `$1ca` | stop |
| `$80004469` | `$1d4` | set volume: `master = SndVol * 4 - 1` |

**Replay rate**: `dtg_Timer = $1bc0` = 7104 CIA-B ticks →
`709379 / 7104 = 99.856 Hz`. This is a ~100 Hz driver, not 50 Hz.

## 3. Root pointers

`InitSound` writes four pointers into the player context (`a5`):

| a5 offset | value | meaning |
|---|---|---|
| `+$00` | `$4708` | song table (also the base for track-list offsets) |
| `+$04` | `$4838` | sequence table (base for pattern offsets) |
| `+$08` | `$1220` | instrument table, `$30` bytes per entry |
| `+$0c` | `$261c` | waveform pointer table |
| `+$10` | `$0e20` | 1 024-byte noise sample (statically initialised) |

`$0e14` (= noise − `$0c`) is a two-byte zero word used as the "loop to
silence" target for one-shot instruments.

## 4. Song table — 12 bytes per subsong, at `$4708`

```
+0  byte  priority          (a song only starts if prio >= current prio)
+1  byte  speed             (replay frames per song tick; 12 here => 8.32 ticks/s)
+2  byte  next song         (0 = stop, else chain — every music subsong loops itself)
+3  byte  unused
+4  word  voice 0 track-list offset, relative to the song table (0 = voice unused)
+6  word  voice 1
+8  word  voice 2
+10 word  voice 3
```

Five subsongs are exported. A sixth well-formed entry follows; it is the
in-game SFX bank and references waveforms that were stripped out of this rip.

A **track list** is a byte string of pattern numbers terminated by `$FF`
(`$FE` = this voice stops but the song continues).

## 5. Sequence table at `$4838`

Word array indexed by pattern number; each word is a byte offset from `$4838`
to the pattern's byte stream.

### Pattern byte stream

| byte | meaning |
|---|---|
| `$00`–`$7F` | **note**: index into the period table. Triggers the selected instrument, then sets the pitch (through portamento if armed). Consumes one note-length. |
| `$80`–`$AF` | set note length to `n − $7F` (1…48 song ticks) |
| `$B0`–`$CF` | **trigger instrument** `n − $AF` at its own pitch (no note). Consumes one note-length. Used for drums. |
| `$D0`–`$EF` | select instrument `n − $CF` for subsequent notes |
| `$F0`–`$F8` | set portamento speed (right-shift, 0 = off) |
| `$FE` | rest — consume one note-length |
| `$F9`–`$FD`, `$FF` | end of pattern → advance the track list |

A trigger only happens if the new instrument's priority ≥ the priority of the
instrument currently sounding on that voice (this is how in-game SFX steal
channels from the music).

## 6. Waveform table at `$261c`

33 longwords, each a **signed offset relative to the table base**, pointing at
a sample. Zero = absent. Each sample is preceded by an 8-byte header:

```
'S' 'S'  word ????   long length      <sample bytes, signed 8-bit>
         ^ table entry points here ---^
```

The pointer targets the **low word of the length**, so the driver reads
`len = (a0)+`, sets `AUDxLEN = len >> 1` and `AUDxLC = a0`.

22 waveforms are present: seventeen short single-cycle shapes (8–128 bytes) and
four sampled drums/leads (1 280, 720, 3 400, 2 220 bytes).

## 7. Instrument table at `$1220` — `$30` bytes per instrument

```
+$00..$1D  three 10-byte pitch-envelope steps (see below)
+$1E  byte   priority
+$1F  byte   waveform index; negative = white noise
+$20  byte   envelope repeat count; 0 = "no attack", jump straight to full volume
+$21  byte   attack length (frames)      +$22  byte  attack delta   (signed)
+$23  byte   decay length                +$24  byte  decay delta    (signed)
+$26  word   sustain length              +$28  byte  sustain delta  (negated on load)
+$29  byte   sustain flip period (delta is negated every n frames → tremolo)
+$2A  byte   release delta (signed)
+$2B  byte   number of pitch steps (1 = the note pitch only)
+$2C  byte   vibrato delay               +$2D  byte  vibrato speed
+$2E  byte   vibrato depth
+$2F  byte   chain: instrument to trigger when this one finishes (0 = stop)
+$25  byte   pitch shift — AUDxPER = pitchvalue >> shift
```

### Pitch-envelope step (10 bytes)

```
+0  word  starting value  (for step 0 this is overwritten by the note's period)
+2  word  per-tick delta
+4  byte  outer repeat count
+5  byte  inner repeat count
+6  byte  frames between updates
+7  byte  delta acceleration (added to the delta each update, signed)
+8  byte  flags: bit0 reset value to start, bit1 negate delta,
                 bit2 shorten the inner count (min 8)
```

Steps run as a nested loop; after the last step it wraps back to step 0. Since
only step 0 receives the note's pitch and steps 1+ hold absolute values, a
multi-step instrument is a fixed arpeggio/chord (instruments 6–11 are exactly
that — their step values are literal period-table entries).

The 16-bit pitch value is right-shifted by `+$25` to give the Paula period,
which is why the period table holds large numbers (see §8).

## 8. Period table at `$01ee`

84 words, `61156 / 2^(n/12)` — a full 7-octave equal-tempered table at 16-bit
precision. `AUDxPER = table[note] >> instrument.pitch_shift`.

## 9. Volume

Volume is a 9-bit value; `AUDxVOL = vol >> 3`. The master volume comes from
DeliTracker as `SndVol * 4 − 1`, i.e. **255** at the maximum slider, so the
module tops out at Paula volume 31. Several instruments have envelope peaks of
252 and 254, which confirms 255 as the intended full scale. `--master 511`
gives the full 0..63 Paula range if you prefer it louder.

## 10. Per-frame flow (routine `$442`, 99.856 Hz)

```
master volume fade ($3aa)
if a song start is queued: start it ($4c6)
speed counter -= 1
for each of the 4 voices:
    sequencer      ($4c)  -- reads pattern bytes when the note length expires
    volume envelope($26)  -- DMA off / DMA on / attack / decay / sustain / release
    pitch envelope ($38)  -- nested sweeps + vibrato, writes AUDxPER
    portamento     ($3c)  -- glide towards the target period
    noise          ($34)  -- re-point AUDxLC into the random buffer each frame
if speed counter == 0: reload it
```

Each of the five per-voice slots is a **stored routine pointer** — the driver
is a set of hand-written coroutines, one frame per resumption. The Python
player mirrors this with explicit state names (`e0..e6`, `p0..p4`,
`trk/pat/wait`).

Note-on sequencing costs three frames: frame *N* turns the channel's DMA off,
*N+1* turns it back on with volume 0, *N+2* installs the one-shot→silence loop
and applies the first attack step.

## 11. Noise

Instruments with a negative waveform index set `AUDxLEN = $100` (512 bytes) and
re-point `AUDxLC` at `noise + (rand & $1FE)` **every frame**, so the loop point
jitters through a 1 KB pseudo-random buffer. The buffer is generated at init
from `VHPOSR` with `d0 = (d0 * $AB) mod $7673 + 1`.
