"""
Convert a tw.* (Tony Williams / Nitro) subsong to an SN76489 register log
(VGM) for the BBC Micro.

The Amiga original is four channels of 8-bit PCM; the BBC has three square
tone channels with a 10-bit divider and one noise channel, all with 4-bit
logarithmic volume, from a 4 MHz clock.  So `125000 / N` Hz, a **122 Hz
floor**, and no timbre at all.  Three arrangement decisions follow, all made
automatically, all reported, all overridable:

1. **Which instruments are pitched.**  The driver's instruments are sampled
   sustains and sampled drums with nothing in the data to tell them apart, so
   each one is autocorrelated: a sample that repeats strongly has a
   fundamental and goes to a tone channel, one that does not is percussion
   and goes to the noise channel.  A voice that plays both moves between its
   tone channel and the noise channel as the arrangement intends.

2. **Pitch.**  A sampled instrument's real pitch is
   `(3546895 / period) / cycle_length`, not the Paula rate, and the cycle
   length differs per instrument -- the bass is 251 samples per cycle, the
   lead 127.  Each tone channel then gets **one constant octave shift** taken
   from the low end of its own range, so the part clears 122 Hz without
   collapsing its octave jumps.  `--transpose` shifts everything on top.

3. **Percussion timbre.**  Each drum's noise shift rate comes from the
   zero-crossing rate of its sample.

Output is VGM 1.50 at 50 Hz -- the driver's own frame rate, so the log is
frame-for-frame with the Amiga.  `--rate 100` doubles it for a 100 Hz player.
"""
import sys, os, struct, argparse, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tw import Module, Player, PAL_CLK

SN_CLOCK = 4000000               # BBC Micro
SN_BASE = SN_CLOCK / 32.0        # 125000 -- f = SN_BASE / N
NOISE_RATES = [SN_CLOCK / 16.0 / 512, SN_CLOCK / 16.0 / 1024,
               SN_CLOCK / 16.0 / 2048]        # 488 / 244 / 122 Hz
TONAL = 0.70                     # autocorrelation above this = pitched


def analyse_instruments(mod, maxlag=1200, window=6000):
    """-> {inst: dict(perc=bool, cycle=int, r=float, zcr=float)}"""
    info = {}
    for i, (s, ln, lp, ll) in enumerate(mod.inst):
        n = ln * 2
        if n < 64:
            info[i] = dict(perc=True, cycle=1, r=0.0, zcr=0.0)
            continue
        x = [b - 256 if b > 127 else b for b in mod.img[s:s + min(n, window)]]
        e = sum(v * v for v in x) or 1
        best, rs = (16, -9.0), {}
        for p in range(16, max(17, min(maxlag, len(x) // 3))):
            r = sum(x[k] * x[k + p] for k in range(0, len(x) - p, 3)) / \
                (sum(x[k] * x[k] for k in range(0, len(x) - p, 3)) or 1)
            rs[p] = r
            if r > best[1]:
                best = (p, r)
        # the peak may be a multiple of the fundamental, so try the exact
        # submultiples of it -- but nothing else, or a hi-hat's short-lag
        # self-similarity gets mistaken for a pitch
        cyc = best[0]
        for k in range(8, 1, -1):
            p = best[0] // k
            if p >= 16 and rs.get(p, -9) >= 0.9 * best[1]:
                cyc = p
                break
        zc = sum(1 for k in range(1, len(x)) if (x[k - 1] < 0) != (x[k] < 0))
        zcr = zc / (len(x) / (PAL_CLK / 428.0)) / 2.0
        info[i] = dict(perc=best[1] < TONAL, cycle=cyc, r=best[1], zcr=zcr)
    return info


def noise_rate_for(inf):
    z = inf['zcr'] or 1.0
    return min(range(3), key=lambda r: abs(math.log(z / NOISE_RATES[r])))


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


def attenuation(v, vmax=64.0):
    if v <= 0:
        return 15
    db = -20.0 * math.log10(min(1.0, v / float(vmax)))
    return max(0, min(15, int(round(db / 2.0))))


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
    ap.add_argument('--rate', type=int, default=50, choices=(50, 100))
    ap.add_argument('--transpose', type=int, default=0, help='semitones')
    ap.add_argument('--tone-voices', default=None,
                    help='comma-separated driver voices for the tone channels')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()

    mod = Module(a.module)
    info = analyse_instruments(mod)
    ratio = 2.0 ** (a.transpose / 12.0)
    if a.verbose:
        for i, inf in sorted(info.items()):
            if mod.inst[i][1]:
                print('  inst %-2d %s  cycle %4d  r=%.2f  zcr %6.0f Hz'
                      % (i, 'perc' if inf['perc'] else 'tone',
                         inf['cycle'], inf['r'], inf['zcr']))

    # ---- capture the song frame by frame ----------------------------------
    pl = Player(mod, out_rate=8000)
    pl.req = a.subsong
    trig = []
    pl.trace = lambda p, v, n: trig.append((p.frames, v.n, v.inst))
    frames = []
    seen, looped = {}, None
    while pl.frames < 200000:
        pl.frame()
        p = pl.paula
        frames.append([(pl.v[c].inst, p.per[c], p.vol[c], p.dma[c])
                       for c in range(4)])
        if pl.isrow:
            key = pl.rowstate()
            if key in seen:
                break
            seen[key] = pl.frames
        if pl.song == 0:
            break
    nframes = len(frames)

    # ---- decide which driver voices own the tone channels -----------------
    tone_frames = [0] * 4
    perc_frames = [0] * 4
    for fr in frames:
        for c, (i, per, vol, dma) in enumerate(fr):
            if not dma or vol == 0 or i not in info:
                continue
            if info[i]['perc']:
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

    print('  tone frames per voice %s, percussion %s'
          % (tone_frames, perc_frames))
    print('  tone channels: %s'
          % ', '.join('SN%d <- voice %d' % (i, v)
                      for v, i in sorted(chan_of.items())))
    if dropped:
        print('  DROPPED tone content of voice(s) %s (only 3 tone channels)'
              % dropped)

    # ---- one constant octave shift per tone channel -----------------------
    shift = {}
    for v in tonev:
        hz = []
        for fr in frames:
            i, per, vol, dma = fr[v]
            if dma and vol and i in info and not info[i]['perc']:
                hz.append((PAL_CLK / max(per, 1)) / info[i]['cycle'] * ratio)
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

    nrate = {i: noise_rate_for(info[i]) for i in info if info[i]['perc']}
    if a.verbose:
        for i in sorted({t[2] for t in trig if info.get(t[2], {}).get('perc')}):
            print('  perc inst %-2d -> noise rate %d (%d Hz)'
                  % (i, nrate[i], NOISE_RATES[nrate[i]]))

    # ---- emit -------------------------------------------------------------
    sn = SN()
    reps = 1 if a.rate == 50 else 2
    waitcode = b'\x63' if a.rate == 50 else b'\x61' + struct.pack('<H', 441)
    trig_by_frame = {}
    for f, ch, i in trig:
        trig_by_frame.setdefault(f, {})[ch] = i
    lastperc = None
    folded = nout = 0

    for f in range(nframes):
        fr = frames[f]
        for ch, i in trig_by_frame.get(f + 1, {}).items():
            if info.get(i, {}).get('perc'):
                lastperc = ch
        for v, chan in chan_of.items():
            i, per, vol, dma = fr[v]
            if not dma or vol == 0 or i not in info or info[i]['perc']:
                sn.set_vol(chan, 15)
                continue
            hz = (PAL_CLK / max(per, 1)) / info[i]['cycle'] * ratio
            hz *= 2 ** shift[v]
            n = SN_BASE / hz
            if n > 1023:
                n = 1023          # below the BBC's 122 Hz floor
                folded += 1
            sn.set_tone(chan, n)
            sn.set_vol(chan, attenuation(vol))
        v = lastperc
        done = False
        if v is not None:
            i, per, vol, dma = fr[v]
            if dma and vol and i in info and info[i]['perc']:
                sn.set_noise(0x04 | nrate.get(i, 0))
                sn.set_vol(3, attenuation(vol))
                nout += 1
                done = True
        if not done:
            sn.set_vol(3, 15)
        for _ in range(reps):
            sn.wait(waitcode)
    sn.out += b'\x66'

    data = bytes(sn.out)
    tags = gd3(['Nitro (subsong %d)' % a.subsong, '', 'Nitro', '',
                'Psygnosis', '1990',
                'Tony Williams; converted from the Amiga TW driver', ''])
    total = int(nframes * (44100.0 / 50.0))
    hdr = bytearray(0x40)
    hdr[0x00:0x04] = b'Vgm '
    struct.pack_into('<I', hdr, 0x08, 0x150)
    struct.pack_into('<I', hdr, 0x0c, SN_CLOCK)
    struct.pack_into('<I', hdr, 0x18, total)
    struct.pack_into('<I', hdr, 0x1c, 0x40 - 0x1c)          # loop to the start
    struct.pack_into('<I', hdr, 0x20, total)
    struct.pack_into('<I', hdr, 0x24, a.rate)
    struct.pack_into('<H', hdr, 0x28, 0x0009)               # SN feedback
    hdr[0x2a] = 16                                          # shift width
    struct.pack_into('<I', hdr, 0x34, 0x40 - 0x34)
    struct.pack_into('<I', hdr, 0x14, 0x40 + len(data) - 0x14)
    struct.pack_into('<I', hdr, 0x04, 0x40 + len(data) + len(tags) - 4)

    out = a.out or 'nitro_sub%d.vgm' % a.subsong
    open(out, 'wb').write(bytes(hdr) + data + tags)
    print('%s  %d bytes  %d frames @ %d Hz (%.1fs)  %d frames at the 122 Hz '
          'floor  %d noise frames'
          % (out, 0x40 + len(data) + len(tags), nframes * reps, a.rate,
             nframes / 50.0, folded, nout))


if __name__ == '__main__':
    main()
