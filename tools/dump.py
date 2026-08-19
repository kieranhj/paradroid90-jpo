"""Human-readable dump of a Paradroid90 JPO module: songs, tracks, patterns,
instruments, waveforms.  This is the conversion-source view of the data."""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jpo import load_cust

NAMES = ['C-', 'C#', 'D-', 'D#', 'E-', 'F-', 'F#', 'G-', 'G#', 'A-', 'A#', 'B-']


def notename(n):
    return '%s%d' % (NAMES[n % 12], n // 12)


def decode_pattern(m, addr, limit=4096):
    """Return a list of (kind, value) tuples for one pattern."""
    out = []
    a = addr
    while a - addr < limit:
        d0 = m.b(a)
        a += 1
        if d0 < 0x80:
            out.append(('note', d0))
        elif d0 < 0xb0:
            out.append(('dur', d0 - 0x7f))
        elif d0 < 0xd0:
            out.append(('trig', d0 - 0xb0 + 1))
        elif d0 < 0xf0:
            out.append(('inst', d0 - 0xd0 + 1))
        elif d0 < 0xf9:
            out.append(('porta', d0 - 0xf0))
        elif d0 == 0xfe:
            out.append(('rest', 0))
        else:
            out.append(('end', d0))
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('module')
    ap.add_argument('--patterns', action='store_true')
    a = ap.parse_args()
    m = load_cust(a.module)

    print('== module map ============================================')
    print('  image size      %6d bytes' % len(m.img))
    print('  song table      $%04x' % m.songs)
    print('  sequence table  $%04x' % m.seqtab)
    print('  instrument tab  $%04x' % m.instrs)
    print('  waveform table  $%04x' % m.wavetab)
    print('  noise sample    $%04x' % m.noise)
    print('  period table    $%04x' % m.pertab)
    print('  replay timer    $%04x  (%.3f Hz)' % (m.timer, 709379.0 / m.timer))
    print('  subsongs        1..%d' % m.nsongs)

    print()
    print('== songs =================================================')
    used_pat = set()
    for s in range(m.nsongs):
        o = m.songs + s * 12
        trk = [m.w(o + 4 + i * 2) for i in range(4)]
        print(' song %d: prio=%d speed=%d next=%d' %
              (s + 1, m.b(o), m.b(o + 1), m.b(o + 2)))
        for v in range(4):
            if not trk[v]:
                print('   voice %d: --' % v)
                continue
            p = m.songs + trk[v]
            seq = []
            while m.b(p) != 0xff:
                seq.append(m.b(p))
                used_pat.add(m.b(p))
                p += 1
            print('   voice %d: %s' % (v, ' '.join('%02x' % x for x in seq)))

    print()
    print('== waveforms =============================================')
    for i in range(22):
        rel = struct.unpack_from('>i', m.img, m.wavetab + i * 4)[0]
        if rel == 0:
            continue
        w = m.wavetab + rel
        print(' wf %-3d $%04x %5d bytes' % (i, w + 2, m.w(w)))

    print()
    print('== instruments ===========================================')
    for i in range(32):
        o = m.instrs + i * 0x30
        wf = m.sb(o + 0x1f)
        if wf >= 0 and struct.unpack_from('>i', m.img, m.wavetab + wf * 4)[0] == 0:
            continue                      # SFX-only instrument, sample absent
        print(' inst %-3d wf=%-4s prio=%-3d pshift=%-2d cycles=%-3d chain=%-3d steps=%d'
              % (i + 1, 'NOISE' if wf < 0 else wf, m.b(o + 0x1e), m.b(o + 0x25),
                 m.b(o + 0x20), m.b(o + 0x2f), m.b(o + 0x2b)))
        print('     vol   A %3d x %+4d   D %3d x %+4d   S %3d x %+4d rep %d   R %+4d'
              % (m.b(o + 0x21), m.sb(o + 0x22), m.b(o + 0x23), m.sb(o + 0x24),
                 m.w(o + 0x26), -m.sb(o + 0x28), m.b(o + 0x29), m.sb(o + 0x2a)))
        if m.b(o + 0x2d):
            print('     vib   delay %d speed %d depth %d'
                  % (m.b(o + 0x2c), m.b(o + 0x2d), m.b(o + 0x2e)))
        for s in range(m.b(o + 0x2b)):
            e = o + s * 10
            dl = m.w(e + 2)
            dl = dl - 0x10000 if dl > 0x7fff else dl
            print('     pitch%d base=%-6d step=%+-6d outer=%-3d inner=%-4d ticks=%-3d '
                  'accel=%+-3d flags=%02x' %
                  (s, m.w(e), dl, m.b(e + 4), m.b(e + 5), m.b(e + 6),
                   m.sb(e + 7), m.b(e + 8)))

    if a.patterns:
        print()
        print('== patterns ==============================================')
        for p in sorted(used_pat):
            addr = m.seqtab + m.w(m.seqtab + p * 2)
            ev = decode_pattern(m, addr)
            txt = []
            for k, v in ev:
                if k == 'note':
                    txt.append(notename(v))
                elif k == 'dur':
                    txt.append('[%d]' % v)
                elif k == 'trig':
                    txt.append('!%d' % v)
                elif k == 'inst':
                    txt.append('i%d' % v)
                elif k == 'porta':
                    txt.append('p%d' % v)
                elif k == 'rest':
                    txt.append('...')
                else:
                    txt.append('|')
            print(' pat %02x $%04x: %s' % (p, addr, ' '.join(txt)))


if __name__ == '__main__':
    main()
