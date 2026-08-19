"""
Convert a Paradroid90 JPO subsong to a 4-channel ProTracker module.

Strategy
--------
The JPO driver runs at 99.856 Hz with a song tick every `speed` frames
(12 here) -- 0.1202 s.  A default ProTracker row (speed 6, BPM 125) is
0.1200 s, so **one song tick == one MOD row** with no tempo commands at all
and a 0.15 % drift.  Note events always land exactly on a row.

Instruments are split in two:

* **static-pitch instruments** (the pitch envelope holds one value for the
  whole note, within a quarter-tone) become a looped MOD sample built from
  the raw JPO waveform.  The pattern carries the *raw Paula period*, so the
  pitch is bit-exact, and the volume envelope is written out as `Cxx`.

* **everything else** -- drums triggered with no note, pitch sweeps, and the
  fixed-chord arpeggio instruments -- is pre-rendered offline, envelope and
  sweep baked in, into a one-shot sample per (instrument, note) pair that the
  song actually uses.  The pattern just triggers it at the reference period.

What is approximated: instrument vibrato and sub-quarter-tone pitch wobble on
static instruments are dropped, volume resolution drops from 99.86 Hz to
8.33 Hz on static instruments, and the tempo is 0.15 % fast.
"""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jpo import load_cust, Player, Paula, Voice, PAL_CLK

REF_PERIOD = 428                 # pre-rendered samples play back at this period
REF_RATE = PAL_CLK / REF_PERIOD  # 8287 Hz
MAX_RENDER = 1.5                 # seconds
QUARTER_TONE = 2 ** (1 / 24.0) - 1


# ---------------------------------------------------------------------------
def simulate(mod, instoff, note, master, maxframes):
    """Run one instrument in isolation; return [(period, vol), ...] per frame."""
    pl = Player(mod, master=master)
    v = pl.voices[0]
    pl.note_on(v, instoff, mod.b(mod.instrs + instoff + 0x1e))
    if note is not None:
        pl.set_note(v, mod.w(mod.pertab + note * 2))
    trace = []
    for _ in range(maxframes):
        pl.env_tick(v)
        pl.pitch_tick(v)
        if v.portstate == 'port':
            pl.port_tick(v)
        if v.noisestate == 'noise':
            pl.noise_tick(v)
        if v.envstate is None:
            break
        trace.append((pl.paula.per[0], pl.paula.vol[0]))
    return trace


def render_oneshot(mod, instoff, note, master, gain):
    """Render an instrument+note to 8-bit signed sample data at REF_RATE."""
    pl = Player(mod, master=master, out_rate=int(REF_RATE))
    v = pl.voices[0]
    pl.note_on(v, instoff, mod.b(mod.instrs + instoff + 0x1e))
    if note is not None:
        pl.set_note(v, mod.w(mod.pertab + note * 2))
    spf = REF_RATE / pl.rate
    buf = [[0.0] * (int(spf) + 2) for _ in range(4)]
    out = []
    carry = 0.0
    limit = int(MAX_RENDER * pl.rate)
    for _ in range(limit):
        pl.env_tick(v)
        pl.pitch_tick(v)
        if v.portstate == 'port':
            pl.port_tick(v)
        if v.noisestate == 'noise':
            pl.noise_tick(v)
        carry += spf
        n = int(carry)
        carry -= n
        pl.paula.render(n, buf)
        out.extend(buf[0][:n])
        if v.envstate is None:
            break
    # buf[] holds sample*volume with volume 0..64 -> scale to +-127
    data = bytearray()
    for x in out:
        s = int(round(x * gain / 64.0))
        data.append(max(-128, min(127, s)) & 0xff)
    while len(data) % 2:
        data.append(0)
    data[-2:] = b'\0\0'                      # 2-byte silent loop at the end
    return bytes(data)


def octave_down(data, k):
    """Lengthen a single-cycle waveform by 2**k with cyclic linear
    interpolation, so the same pitch needs a 2**k smaller Paula period."""
    if k == 0:
        return data
    src = [b - 256 if b > 127 else b for b in data]
    n = len(src)
    f = 1 << k
    out = bytearray()
    for i in range(n * f):
        p = i / f
        i0 = int(p) % n
        i1 = (i0 + 1) % n
        t = p - int(p)
        out.append(int(round(src[i0] * (1 - t) + src[i1] * t)) & 0xff)
    return bytes(out)


# ---------------------------------------------------------------------------
class ModBuilder:
    def __init__(self, title):
        self.title = title
        self.samples = []                    # (name, bytes, repeat, replen, vol)

    def add_sample(self, name, data, repeat, replen, vol=64):
        if len(self.samples) >= 31:
            raise RuntimeError('more than 31 samples needed')
        self.samples.append((name, data, repeat, replen, vol))
        return len(self.samples)             # 1-based

    def write(self, path, orders, patterns):
        out = bytearray()
        out += self.title.encode('ascii', 'replace')[:20].ljust(20, b'\0')
        for i in range(31):
            if i < len(self.samples):
                nm, d, rep, rl, vol = self.samples[i]
                out += nm.encode('ascii', 'replace')[:22].ljust(22, b'\0')
                out += struct.pack('>H', len(d) // 2)
                out += bytes([0, vol])
                out += struct.pack('>HH', rep, rl)
            else:
                out += b'\0' * 22 + struct.pack('>H', 0) + bytes([0, 0]) + \
                       struct.pack('>HH', 0, 1)
        out += bytes([len(orders), 0x7f])
        out += bytes(orders).ljust(128, b'\0')
        out += b'M.K.'
        for p in patterns:
            out += p
        for _, d, _, _, _ in self.samples:
            out += d
        open(path, 'wb').write(out)
        return len(out)


def cell(sample, period, effect, param):
    return bytes([(sample & 0xf0) | ((period >> 8) & 0x0f),
                  period & 0xff,
                  ((sample & 0x0f) << 4) | (effect & 0x0f),
                  param & 0xff])


EMPTY = cell(0, 0, 0, 0)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('module')
    ap.add_argument('-s', '--subsong', type=int, default=1)
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--master', type=int, default=255)
    ap.add_argument('--gain', type=float, default=2.0,
                    help='make up for the 0..31 Paula range the driver uses')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()

    mod = load_cust(a.module)
    speed = mod.b(mod.songs + (a.subsong - 1) * 12 + 1)

    # ---- pass 1: record every frame of the song ---------------------------
    pl = Player(mod, master=a.master)
    events = []                              # (frame, ch, instoff, note)
    pl.trace = lambda f, v, n: events.append((f, v.n, v.instoff, n))
    pl.pending = a.subsong
    frames = []                              # per frame: [(per, vol, dma)] * 4
    ends = 0
    while pl.frames < 200000:
        pl.frame()
        p = pl.paula
        frames.append([(p.per[c], p.vol[c], p.dma[c]) for c in range(4)])
        if pl.songend:
            pl.songend = False
            ends += 1
            break
    nframes = pl.frames

    # ---- pass 2: classify the (instrument, note) pairs used ---------------
    pairs = sorted({(io, n) for _, _, io, n in events})
    static = {}                              # instoff -> True/False
    for io, n in pairs:
        tr = simulate(mod, io, n, a.master, 400)
        pers = [x[0] for x in tr if x[1] > 0]
        if pers and (max(pers) - min(pers)) / min(pers) < QUARTER_TONE \
                and n is not None:
            static[io] = True
        else:
            static.setdefault(io, False)

    builder = ModBuilder('Paradroid90 sub%d' % a.subsong)
    smp_static = {}                          # (instoff, k) -> sample number
    smp_shot = {}                            # (instoff, note) -> sample number

    for io, n in pairs:
        if static.get(io):
            continue
        data = render_oneshot(mod, io, n, a.master, a.gain)
        smp_shot[(io, n)] = builder.add_sample(
            'inst%02d %s' % (io // 0x30 + 1, 'trig' if n is None else 'note%d' % n),
            data, len(data) // 2 - 1, 1)
        if a.verbose:
            print('  one-shot inst%-3d note=%-4s %6d bytes' %
                  (io // 0x30 + 1, n, len(data)))

    # ---- pass 3: lay the frames out on rows -------------------------------
    nrows = (nframes + speed - 1) // speed
    grid = [[EMPTY] * 4 for _ in range(nrows + 1)]
    ev_by_row = {}
    for f, ch, io, n in events:
        ev_by_row.setdefault(f // speed, {})[ch] = (io, n)

    def state(f, ch):
        return frames[max(0, min(f - 1, nframes - 1))][ch]

    def octave_for(per):
        """How many octaves the sample must be stretched to reach PT range."""
        k = 0
        while per > 856 and k < 4:
            per /= 2.0
            k += 1
        return k

    # 3a. which (instrument, octave) sample variants are actually needed
    notes = []                              # (row, ch, io, note, period, vol)
    for row in range(nrows):
        f = max(1, row * speed)
        for ch, (io, n) in ev_by_row.get(row, {}).items():
            per = state(f, ch)[0]
            pk = max(state(f + k, ch)[1] for k in range(speed))
            notes.append((row, ch, io, n, per, pk))
            if static.get(io):
                smp_static.setdefault((io, octave_for(per)), None)

    for (io, k) in sorted(smp_static):
        wf = mod.sb(mod.instrs + io + 0x1f)
        rel = struct.unpack_from('>i', mod.img, mod.wavetab + wf * 4)[0]
        addr = mod.wavetab + rel
        ln = mod.w(addr) & ~1
        data = octave_down(bytes(mod.img[addr + 2:addr + 2 + ln]), k)
        smp_static[(io, k)] = builder.add_sample(
            'inst%02d wave%02d %s' % (io // 0x30 + 1, wf,
                                      'loop' if k == 0 else 'loop -%do' % k),
            data, 0, len(data) // 2)

    # 3b. emit the cells
    lastvol = [-1] * 4
    lastper = [-1] * 4
    isshot = [False] * 4
    notemap = {(r, c): (io, n, per, vol) for r, c, io, n, per, vol in notes}
    for row in range(nrows):
        f = max(1, row * speed)
        for ch in range(4):
            ev = notemap.get((row, ch))
            per, vol, dma = state(f, ch)
            modvol = min(64, int(round(vol * a.gain)))
            if ev is not None:
                io, n, p0, pk = ev
                if static.get(io):
                    k = octave_for(p0)
                    p0 = max(113, min(4095, p0 >> k))
                    modvol = min(64, int(round(pk * a.gain)))
                    grid[row][ch] = cell(smp_static[(io, k)], p0, 0x0c, modvol)
                    isshot[ch] = False
                else:
                    grid[row][ch] = cell(smp_shot[(io, n)], REF_PERIOD, 0, 0)
                    modvol = 64
                    isshot[ch] = True
                lastper[ch] = p0
                lastvol[ch] = modvol
                continue
            if lastper[ch] < 0:
                continue
            if not dma or vol == 0:
                if lastvol[ch] != 0:
                    grid[row][ch] = cell(0, 0, 0x0c, 0)
                    lastvol[ch] = 0
                continue
            if not isshot[ch] and modvol != lastvol[ch]:
                grid[row][ch] = cell(0, 0, 0x0c, modvol)
                lastvol[ch] = modvol

    # ---- pass 4: pack into 64-row patterns --------------------------------
    patterns = []
    orders = []
    seen = {}
    for base in range(0, nrows, 64):
        pat = bytearray()
        for r in range(64):
            for ch in range(4):
                pat += grid[base + r][ch] if base + r < nrows else EMPTY
        key = bytes(pat)
        if key in seen:
            orders.append(seen[key])
        else:
            seen[key] = len(patterns)
            orders.append(len(patterns))
            patterns.append(key)
    if len(patterns) > 100:
        print('warning: %d patterns, some players cap at 100' % len(patterns))

    out = a.out or 'paradroid90_sub%d.mod' % a.subsong
    size = builder.write(out, orders, patterns)
    print('%s  %d bytes  %d samples  %d patterns  %d orders  %d rows (%.1fs)' %
          (out, size, len(builder.samples), len(patterns), len(orders),
           nrows, nrows * 0.12))


if __name__ == '__main__':
    main()
