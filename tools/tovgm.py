"""
Convert a Paradroid90 JPO subsong to an SN76489 register log (VGM) for the
BBC Micro.

The BBC's SN76489 runs from a 4 MHz clock, so a tone channel's frequency is
`125000 / N` with a 10-bit divider: **122 Hz is the lowest note it can play**
and there are only three tone channels plus one noise channel, with 4-bit
logarithmic volume.  That forces three arrangement decisions, all made
automatically here and all overridable:

1. **Voice → channel.**  An instrument whose waveform is one of the four
   sampled drums, or the noise waveform, is percussion and goes to the noise
   channel; anything built on a single-cycle waveform keeps a real pitch and
   goes to a tone channel.  A JPO voice that alternates between the two (the
   bass channel also fires the snare) therefore moves between a tone channel
   and the noise channel exactly as the arrangement intends.  The three
   busiest tone voices get channels 0-2; any others are dropped and reported.

2. **Pitch.**  A single-cycle waveform's real pitch is
   `(3546895 / period) / cycle_length`, not the Paula rate.  Each tone
   channel then gets **one constant octave shift**, chosen from the low end
   of its own range, so the part fits above 122 Hz without collapsing the
   octave jumps in the bass riff -- folding note by note would put C-1 and
   C-2 on the same pitch.  `--transpose` shifts the whole tune on top.

3. **Percussion timbre.**  Each drum's noise rate is picked from the
   zero-crossing rate of its rendered waveform, so bright hits get the fast
   shift rate and the kick gets the slow one.

Output is a VGM 1.50 log at 50 Hz (`--rate 100` for a 100 Hz player), which
is what the BBC Micro VGM players consume, and which foobar2000 / VGMPlay
will also play directly.
"""
import sys, os, struct, argparse, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jpo import load_cust, Player, PAL_CLK

SN_CLOCK = 4000000               # BBC Micro
SN_BASE = SN_CLOCK / 32.0        # 125000 -- f = SN_BASE / N
NOISE_RATES = [SN_CLOCK / 16.0 / 512, SN_CLOCK / 16.0 / 1024,
               SN_CLOCK / 16.0 / 2048]        # 488 / 244 / 122 Hz


# ---------------------------------------------------------------------------
def cycle_length(data):
    """Shortest repeating period of a single-cycle waveform."""
    n = len(data)
    best = (float('inf'), n)
    for p in range(1, n + 1):
        if n % p:
            continue
        err = sum((data[i] - data[i % p]) ** 2 for i in range(n)) / n
        if err < best[0] - 1e-9:
            best = (err, p)
    return best[1]


def analyse_instruments(mod):
    """-> {instoff: dict(perc=bool, cycle=int, wf=int)}"""
    info = {}
    for i in range(32):
        io = i * 0x30
        wf = mod.sb(mod.instrs + io + 0x1f)
        if wf >= 0:
            rel = struct.unpack_from('>i', mod.img, mod.wavetab + wf * 4)[0]
            if rel == 0:
                continue
            addr = mod.wavetab + rel
            ln = mod.w(addr)
            data = [b - 256 if b > 127 else b for b in mod.img[addr + 2:addr + 2 + ln]]
            perc = ln > 256
            cyc = ln if perc else cycle_length(data)
        else:
            perc, cyc, data = True, 1, []
        info[io] = dict(perc=perc, cycle=cyc, wf=wf, data=data)
    return info


def noise_rate_for(mod, io, master):
    """Pick an SN noise shift rate from the brightness of the rendered drum."""
    from tomod import render_oneshot, REF_RATE
    data = render_oneshot(mod, io, None, master, 1.0)
    s = [b - 256 if b > 127 else b for b in data]
    zc = sum(1 for i in range(1, len(s)) if (s[i - 1] < 0) != (s[i] < 0))
    zcr = zc / (len(s) / REF_RATE) / 2.0 if len(s) > 8 else 0
    return min(range(3), key=lambda r: abs(math.log((zcr or 1) / NOISE_RATES[r])))


# ---------------------------------------------------------------------------
class SN:
    """Tracks chip state and emits only the writes that change something."""

    def __init__(self):
        self.tone = [-1] * 4
        self.vol = [-1] * 4
        self.out = bytearray()

    def set_tone(self, ch, n):
        n = max(1, min(1023, int(round(n))))
        if self.tone[ch] != n:
            self.tone[ch] = n
            self.out += bytes([0x50, 0x80 | (ch << 5) | (n & 0x0f),
                               0x50, (n >> 4) & 0x3f])

    def set_noise(self, mode):
        if self.tone[3] != mode:
            self.tone[3] = mode
            self.out += bytes([0x50, 0xe0 | (mode & 0x07)])

    def set_vol(self, ch, att):
        att = max(0, min(15, int(att)))
        if self.vol[ch] != att:
            self.vol[ch] = att
            self.out += bytes([0x50, 0x90 | (ch << 5) | att])

    def wait(self, code):
        self.out += code


def attenuation(v, vmax):
    if v <= 0:
        return 15
    db = -20.0 * math.log10(min(1.0, v / float(vmax)))
    return max(0, min(15, int(round(db / 2.0))))


# ---------------------------------------------------------------------------
def gd3(fields):
    body = b''
    for f in fields:
        body += f.encode('utf-16-le') + b'\0\0'
    return b'Gd3 ' + struct.pack('<II', 0x100, len(body)) + body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('module')
    ap.add_argument('-s', '--subsong', type=int, default=1)
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--master', type=int, default=255)
    ap.add_argument('--rate', type=int, default=50, choices=(50, 100))
    ap.add_argument('--transpose', type=int, default=0, help='semitones')
    ap.add_argument('--tone-voices', default=None,
                    help='comma-separated JPO voices to put on tone channels')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()

    mod = load_cust(a.module)
    info = analyse_instruments(mod)
    ratio = 2.0 ** (a.transpose / 12.0)

    # ---- capture the song frame by frame ----------------------------------
    pl = Player(mod, master=a.master)
    trig = []
    pl.trace = lambda f, v, n: trig.append((f, v.n, v.instoff))
    pl.pending = a.subsong
    frames = []
    while pl.frames < 200000:
        pl.frame()
        p = pl.paula
        frames.append([(pl.voices[c].instoff, p.per[c], p.vol[c], p.dma[c])
                       for c in range(4)])
        if pl.songend:
            break
    nframes = len(frames)

    # ---- decide which JPO voices own the tone channels --------------------
    tone_frames = [0] * 4
    perc_frames = [0] * 4
    for fr in frames:
        for c, (io, per, vol, dma) in enumerate(fr):
            if not dma or vol == 0 or io == 0xffff or io not in info:
                continue
            if info[io]['perc']:
                perc_frames[c] += 1
            else:
                tone_frames[c] += 1
    if a.tone_voices:
        tonev = [int(x) for x in a.tone_voices.split(',')]
    else:
        tonev = sorted(range(4), key=lambda c: -tone_frames[c])
        tonev = [c for c in tonev if tone_frames[c] > 0][:3]
        tonev.sort()
    dropped = [c for c in range(4) if tone_frames[c] > 0 and c not in tonev]
    chan_of = {v: i for i, v in enumerate(tonev)}

    print('  tone frames per JPO voice %s, percussion %s' %
          (tone_frames, perc_frames))
    print('  tone channels: %s' % ', '.join('SN%d <- voice %d' % (i, v)
                                            for v, i in sorted(chan_of.items())))
    if dropped:
        print('  DROPPED tone content of voice(s) %s (only 3 tone channels)'
              % dropped)

    # ---- one constant octave shift per tone channel -----------------------
    # Folding each note individually would collapse the bass riff's octave
    # jumps onto the same pitch, so every channel gets a single shift chosen
    # from its low end (2nd percentile, so a rare downward sweep does not
    # transpose the whole part).
    shift = {}
    for v in tonev:
        hz = []
        for fr in frames:
            io, per, vol, dma = fr[v]
            if dma and vol and io in info and not info[io]['perc']:
                hz.append((PAL_CLK / max(per, 1)) / info[io]['cycle'] * ratio)
        if not hz:
            shift[v] = 0
            continue
        hz.sort()
        low = hz[len(hz) // 50]
        k = 0
        while low * (2 ** k) < SN_BASE / 1023.0 and k < 5:
            k += 1
        shift[v] = k
        print('  voice %d: %.1f..%.1f Hz -> +%d octave(s)'
              % (v, hz[0], hz[-1], k))

    # ---- noise timbre per percussion instrument ---------------------------
    percinsts = sorted({io for _, _, io in trig
                        if io in info and info[io]['perc']})
    nrate = {}
    for io in percinsts:
        nrate[io] = noise_rate_for(mod, io, a.master)
        if a.verbose:
            print('  perc inst %-3d -> noise rate %d (%d Hz)' %
                  (io // 0x30 + 1, nrate[io], NOISE_RATES[nrate[io]]))

    # ---- emit -------------------------------------------------------------
    sn = SN()
    step = 2 if a.rate == 50 else 1
    waitcode = b'\x63' if a.rate == 50 else b'\x61' + struct.pack('<H', 441)
    vmax = a.master >> 3
    folded = 0
    trig_by_frame = {}
    for f, ch, io in trig:
        trig_by_frame.setdefault(f, {})[ch] = io
    lastperc = None
    nout = 0

    for f in range(0, nframes, step):
        fr = frames[f]
        for c in range(1, step + 1):
            for ch, io in trig_by_frame.get(f + c, {}).items():
                if io in info and info[io]['perc']:
                    lastperc = ch
        # tone channels
        for v, chan in chan_of.items():
            io, per, vol, dma = fr[v]
            if not dma or vol == 0 or io not in info or info[io]['perc']:
                sn.set_vol(chan, 15)
                continue
            hz = (PAL_CLK / max(per, 1)) / info[io]['cycle'] * ratio
            hz *= 2 ** shift[v]
            n = SN_BASE / hz
            if n > 1023:
                n = 1023          # below the BBC's 122 Hz floor
                folded += 1
            sn.set_tone(chan, n)
            sn.set_vol(chan, attenuation(vol, vmax))
        # noise channel
        v = lastperc
        if v is not None:
            io, per, vol, dma = fr[v]
            if dma and vol and io in info and info[io]['perc']:
                sn.set_noise(0x04 | nrate.get(io, 0))
                sn.set_vol(3, attenuation(vol, vmax))
                nout += 1
            else:
                sn.set_vol(3, 15)
        else:
            sn.set_vol(3, 15)
        sn.wait(waitcode)
    sn.out += b'\x66'

    data = bytes(sn.out)
    tags = gd3(['Paradroid 90 (subsong %d)' % a.subsong, '',
                'Paradroid 90', '', 'Hewson / Graftgold', '1990',
                'Jason Page; converted from the Amiga JPO driver', ''])
    hdr = bytearray(0x40)
    hdr[0x00:0x04] = b'Vgm '
    struct.pack_into('<I', hdr, 0x08, 0x150)
    struct.pack_into('<I', hdr, 0x0c, SN_CLOCK)
    struct.pack_into('<I', hdr, 0x18, int(nframes / step * (44100.0 / a.rate)))
    struct.pack_into('<I', hdr, 0x1c, 0x40 - 0x1c)          # loop to the start
    struct.pack_into('<I', hdr, 0x20, int(nframes / step * (44100.0 / a.rate)))
    struct.pack_into('<I', hdr, 0x24, a.rate)
    struct.pack_into('<H', hdr, 0x28, 0x0009)               # SN feedback
    hdr[0x2a] = 16                                          # shift width
    struct.pack_into('<I', hdr, 0x34, 0x40 - 0x34)
    struct.pack_into('<I', hdr, 0x14, 0x40 + len(data) - 0x14)
    struct.pack_into('<I', hdr, 0x04, 0x40 + len(data) + len(tags) - 4)

    out = a.out or 'paradroid90_sub%d.vgm' % a.subsong
    open(out, 'wb').write(bytes(hdr) + data + tags)
    print('%s  %d bytes  %d frames @ %d Hz (%.1fs)  %d frames clipped at the '
          '122 Hz floor  %d noise frames' %
          (out, 0x40 + len(data) + len(tags), nframes // step, a.rate,
           nframes / 99.856, folded, nout))


if __name__ == '__main__':
    main()
