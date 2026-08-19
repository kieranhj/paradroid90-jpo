"""Reproduce the measurements quoted in docs/COMPARISON.md.

    python tools/stats.py $MOD [--mod output/mod/paradroid90_sub1.mod]
"""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jpo import load_cust
from dump import decode_pattern


def wave_len(m, i):
    rel = struct.unpack_from('>i', m.img, m.wavetab + i * 4)[0]
    return m.w(m.wavetab + rel) if rel else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('module')
    ap.add_argument('--mod', default='output/mod/paradroid90_sub1.mod')
    ap.add_argument('-s', '--subsong', type=int, default=1)
    a = ap.parse_args()
    m = load_cust(a.module)
    song = a.subsong - 1

    print('== module footprint (flattened image %d bytes) ==' % len(m.img))
    rows = [('replay code + period table + context', 0xe14),
            ('noise sample (chip)', 0x40c),
            ('instrument table (%d x 48)' % ((m.wavetab - m.instrs) // 0x30),
             m.wavetab - m.instrs),
            ('waveform data + headers', m.songs - m.wavetab),
            ('song table + track lists', m.seqtab - m.songs),
            ('pattern byte streams', len(m.img) - m.seqtab)]
    for name, n in rows:
        print('  %-38s %6d' % (name, n))
    single = sum(wave_len(m, i) for i in range(18))
    drums = sum(wave_len(m, i) for i in range(18, 22))
    print('  %-38s %6d  (%d single-cycle + %d drums)'
          % ('...of which raw sample bytes', single + drums, single, drums))

    print()
    print('== subsong %d sequence data ==' % a.subsong)
    used, trk = set(), 0
    for v in range(4):
        off = m.w(m.songs + song * 12 + 4 + v * 2)
        if not off:
            continue
        p = m.songs + off
        while m.b(p) != 0xff:
            used.add(m.b(p))
            p += 1
            trk += 1
        trk += 1
    patbytes = sum(len(decode_pattern(m, m.seqtab + m.w(m.seqtab + p * 2)))
                   for p in used)
    seqtab = 2 * (max(used) + 1)
    total = trk + patbytes + seqtab + 12

    # pattern lengths in song ticks, with the duration state carried over
    print('  track lists       %5d bytes' % trk)
    print('  %2d distinct patterns %5d bytes' % (len(used), patbytes))
    print('  sequence table    %5d bytes' % seqtab)
    for v in range(4):
        off = m.w(m.songs + song * 12 + 4 + v * 2)
        if not off:
            continue
        p = m.songs + off
        seq = []
        while m.b(p) != 0xff:
            seq.append(m.b(p))
            p += 1
        dur, lens = 0, []
        for pat in seq:
            t = 0
            for k, val in decode_pattern(m, m.seqtab + m.w(m.seqtab + pat * 2)):
                if k == 'dur':
                    dur = val
                elif k in ('note', 'trig', 'rest'):
                    t += dur
            lens.append(t)
        print('  voice %d: %2d patterns, tick lengths %s, total %d'
              % (v, len(seq), sorted(set(lens)), sum(lens)))
    secs = sum(lens) * m.b(m.songs + song * 12 + 1) / (709379.0 / m.timer)
    print('  TOTAL sequence    %5d bytes for %.1f s = %.1f bytes/second'
          % (total, secs, total / secs))

    if not os.path.exists(a.mod):
        return
    d = open(a.mod, 'rb').read()
    nord = d[950]
    npat = max(d[952:952 + nord]) + 1
    empty = sum(1 for p in range(npat) for i in range(256)
                if d[1084 + p * 1024 + i * 4:1088 + p * 1024 + i * 4] == b'\0' * 4)
    print()
    print('== %s ==' % a.mod)
    print('  pattern data      %5d bytes (%d x 1024)' % (npat * 1024, npat))
    print('  order list        %5d bytes' % 128)
    print('  sample headers    %5d bytes' % 930)
    print('  sample data       %5d bytes' % (len(d) - 1084 - npat * 1024))
    print('  TOTAL             %5d bytes = %.1f bytes/second'
          % (len(d), len(d) / secs))
    print('  sequence only     %5d bytes = %.1f bytes/second'
          % (npat * 1024 + 128, (npat * 1024 + 128) / secs))
    print('  empty cells       %d / %d (%.0f%%)'
          % (empty, npat * 256, 100.0 * empty / (npat * 256)))


if __name__ == '__main__':
    main()
