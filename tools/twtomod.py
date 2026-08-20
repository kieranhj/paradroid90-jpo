"""Convert a tw.* (Tony Williams / Nitro) subsong to a ProTracker module.

The mapping is unusually direct.  The driver runs off the 50 Hz vertical
blank with a tick divider, so one driver row is exactly `tempo` frames; a
ProTracker row at the default 125 BPM is exactly `speed` frames of 20 ms.
Setting the module speed to the driver tempo makes one driver row one MOD
row with no drift at all.  The driver's period table is the ProTracker
period table, so notes convert bit-exact, and the instruments are ordinary
8-bit PCM one-shots, so they convert byte-exact.

What has to be approximated is the per-frame modulation: the ADSR volume
envelope, vibrato, the release portamento and the arpeggio tables all run at
50 Hz while ProTracker allows one effect per channel per row.  Effects are
chosen per cell in this order:

    note trigger        Cxx  set the envelope's starting volume
    arpeggio running    0xy  from the first two offsets of the driver table
    portamento running  1xx / 2xx  scaled for the speed-1 slide ticks
    vibrato running     4xy  matched to the driver's triangle
    volume changed      Cxx

so a sustained note that both vibrates and decays keeps the vibrato and
holds its volume until the modulation stops.
"""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tw import Module, Player

MAXROWS = 20000


def scan(mod, subsong, master=0x10):
    """Play the subsong and record per-row, per-channel state."""
    pl = Player(mod, out_rate=8000, master=master)
    pl.req = subsong
    pl.isrow = False
    trig = {}

    def tr(p, v, n):
        trig[v.n] = (n, v.inst, v.transpose)
    pl.trace = tr

    rows = []
    seen = {}
    looprow = None
    frames = 0
    while frames < MAXROWS * 8:
        pl.frame()
        frames += 1
        if pl.isrow:
            key = pl.rowstate()
            if key in seen:
                looprow = seen[key]
                break
            seen[key] = len(rows)
            rows.append([dict(trig=trig.pop(v.n, None), vol=pl.volout(v.volume),
                              per=v.period, arp=v.arptab if v.flags & 1 else 0,
                              porta=(v.pstep if (v.flags & 2) and v.pcnt else 0),
                              vib=(v.vibdepth, v.vibspd)
                              if v.vibdepth and
                              ((v.defdur - v.dur) & 0xff) >= v.vibdelay else 0,
                              inst=v.inst)
                         for v in pl.v])
        elif rows:
            for c, v in enumerate(pl.v):
                r = rows[-1][c]
                r['vol'] = max(r['vol'], pl.volout(v.volume))
                if not r['arp'] and v.flags & 1:
                    r['arp'] = v.arptab
                if not r['porta'] and (v.flags & 2) and v.pcnt:
                    r['porta'] = v.pstep
                if not r['vib'] and v.vibdepth and \
                        ((v.defdur - v.dur) & 0xff) >= v.vibdelay:
                    r['vib'] = (v.vibdepth, v.vibspd)
        if pl.song == 0:
            break
    return rows, looprow, pl.tempo


def arp_xy(mod, tab):
    """First two distinct semitone offsets of a driver arpeggio table."""
    seq = []
    p = tab
    for _ in range(32):
        b = mod.b(p)
        p += 1
        v = b & 0x7f
        if v not in seq:
            seq.append(v)
        if b & 0x80:
            break
    seq = [v for v in seq if v] + [0, 0]
    return (min(seq[0], 15) << 4) | min(seq[1], 15)


def build(mod, rows, looprow, tempo, name):
    used = sorted({r['inst'] for row in rows for r in row
                   if r['inst'] is not None and r['inst'] >= 0
                   and mod.inst[r['inst']][1]})
    slot = {i: n + 1 for n, i in enumerate(used)}
    if len(slot) > 31:
        raise SystemExit('more than 31 instruments')

    samples = []
    for i in used:
        s, ln, lp, ll = mod.inst[i]
        data = bytearray(mod.img[s:s + ln * 2])
        if ll * 2 > 32:                      # a real loop
            ro, rl = (lp - s) // 2, ll
        else:                                # one-shot: loop 2 bytes of silence
            data += b'\0\0'
            ro, rl = len(data) // 2 - 1, 1
        samples.append((len(data) // 2, ro, rl, bytes(data)))

    npat = (len(rows) + 63) // 64
    pat = [bytearray(1024) for _ in range(npat)]
    prevvol = [-1] * 4
    vibrun = [False] * 4
    for ri, row in enumerate(rows):
        for c in range(4):
            r = row[c]
            per = eff = arg = smp = 0
            if r['trig'] is not None:
                note, inst, tp = r['trig']
                per = mod.per[((note + tp) * 2 & 0x7f) >> 1]
                smp = slot.get(inst, 0)
                eff, arg = 0xc, min(r['vol'], 64)
                prevvol[c] = arg
                vibrun[c] = False
            elif r['arp']:
                eff, arg = 0x0, arp_xy(mod, r['arp'])
            elif r['porta']:
                d = r['porta']
                step = (d & 0x7fff) if d & 0x8000 else d
                step = int(round(step * tempo / max(tempo - 1, 1)))
                eff = 0x1 if (d & 0x8000) else 0x2   # period down = pitch up
                arg = min(step, 255)
            elif r['vib']:
                depth, spd = r['vib']
                tgt = min(r['vol'], 64)
                if vibrun[c] and tgt != prevvol[c]:
                    # 6xy: keep the vibrato from memory and slide the volume,
                    # which is the only way to have both in one MOD cell
                    n = max(tempo - 1, 1)
                    d = min(max(int(round(abs(tgt - prevvol[c]) / n)), 1), 15)
                    eff = 0x6
                    arg = (d << 4) if tgt > prevvol[c] else d
                    prevvol[c] = min(64, max(0, prevvol[c] +
                                             (d * n if tgt > prevvol[c]
                                              else -d * n)))
                else:
                    x = min(max(int(round(depth * spd / 2.0)), 1), 15)
                    y = min(max(int(round(16.0 / max(spd, 1))), 1), 15)
                    eff, arg = 0x4, (x << 4) | y
                    vibrun[c] = True
            elif min(r['vol'], 64) != prevvol[c]:
                eff, arg = 0xc, min(r['vol'], 64)
                prevvol[c] = arg
            p, k = pat[ri // 64], (ri % 64) * 16 + c * 4
            p[k] = (smp & 0xf0) | ((per >> 8) & 0xf)
            p[k + 1] = per & 0xff
            p[k + 2] = ((smp & 0x0f) << 4) | eff
            p[k + 3] = arg

    # Loop back to the driver's restart row with a Bxx (+ Dyy for the row).
    # A subsong that ends rather than loops (the driver's $83) gets B00, so
    # the module restarts instead of running out into the padding rows.
    if len(rows):
        if looprow is None:
            looprow = 0
        ri = len(rows) - 1
        p, k = pat[ri // 64], (ri % 64) * 16
        p[k + 2] = (p[k + 2] & 0xf0) | 0x0b
        p[k + 3] = looprow // 64
        if looprow % 64:
            y = looprow % 64
            p[k + 6] = (p[k + 6] & 0xf0) | 0x0d
            p[k + 7] = ((y // 10) << 4) | (y % 10)

    out = bytearray()
    out += name.encode('ascii', 'replace')[:20].ljust(20, b'\0')
    for n in range(31):
        if n < len(samples):
            ln, ro, rl, _ = samples[n]
            out += ('sample %d' % (n + 1)).encode().ljust(22, b'\0')
            out += struct.pack('>HBBHH', min(ln, 0xffff), 0, 64, ro, rl)
        else:
            out += b'\0' * 22 + struct.pack('>HBBHH', 0, 0, 0, 0, 1)
    order = list(range(npat))
    out += bytes([len(order), 127])
    out += bytes(order + [0] * (128 - len(order)))
    out += b'M.K.'
    for p in pat:
        out += bytes(p)
    for _, _, _, data in samples:
        out += data
    # speed: one MOD row = one driver row
    return bytes(out), npat, len(slot)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('module')
    ap.add_argument('-s', '--subsong', type=int, default=1)
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('-n', '--name', default=None)
    a = ap.parse_args()

    mod = Module(a.module)
    rows, looprow, tempo = scan(mod, a.subsong)
    name = a.name or 'nitro %d' % a.subsong
    data, npat, nsmp = build(mod, rows, looprow, tempo, name)

    # the module needs the driver tempo as its speed; put Fxx on the first
    # channel of row 0 that is not already carrying an effect
    data = bytearray(data)
    for c in range(4):
        k = 1084 + c * 4
        if data[k + 2] & 0x0f or data[k + 3]:
            continue
        data[k + 2] = (data[k + 2] & 0xf0) | 0x0f
        data[k + 3] = tempo
        break
    else:
        k = 1084 + 12
        data[k + 2] = (data[k + 2] & 0xf0) | 0x0f
        data[k + 3] = tempo
    out = a.out or os.path.splitext(a.module)[0] + '_sub%d.mod' % a.subsong
    open(out, 'wb').write(bytes(data))
    print('%s  %d rows / %d patterns, %d samples, speed %d (%.2f s/row), '
          'loop row %s, %d bytes'
          % (out, len(rows), npat, nsmp, tempo, tempo / 50.0, looprow,
             len(data)))


if __name__ == '__main__':
    main()
