"""Decode a tw.* module: song table, instruments, arpeggios and patterns."""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tw import Module, Player, SONGTAB, INSTAB, ARPTAB, NSONGS, s8

NOTES = ['C-', 'C#', 'D-', 'D#', 'E-', 'F-', 'F#', 'G-', 'G#', 'A-', 'A#', 'B-']


def notename(n):
    return '%s%d' % (NOTES[n % 12], n // 12 + 1) if 0 <= n < 36 else '?%d' % n


def decode_pattern(m, p, limit=4096):
    """Yield (kind, text, size) for each command in one pattern byte stream."""
    out = []
    for _ in range(limit):
        b = m.b(p)
        if b < 0x80:
            out.append((p, 1, 'note %-4s (%d)' % (notename(b), b)))
            p += 1
        elif b >= 0xe0:
            out.append((p, 1, 'dur  %d' % (b - 0xdf)))
            p += 1
        elif b >= 0xd0:
            out.append((p, 1, 'inst %d' % (b - 0xd0)))
            p += 1
        elif b >= 0xc0:
            n1, n2, n3 = m.b(p + 1), m.b(p + 2), m.b(p + 3)
            out.append((p, 4, 'vol  %d  attack +%d x%d every %d  '
                              'decay -%d x%d every %d'
                        % (b - 0xc0, n1 >> 4, n1 & 15, n3 >> 4,
                           n2 >> 4, n2 & 15, n3 & 15)))
            p += 4
        elif b >= 0xb0:
            out.append((p, 1, 'vol  %d' % (b - 0xb0)))
            p += 1
        elif b >= 0xa0:
            out.append((p, 1, 'arp  table %d' % (b - 0xa0)))
            p += 1
        else:
            i = b & 0x1f
            if i in (0, 3):
                out.append((p, 1, 'END' if i == 3 else 'next pattern'))
                return out
            if i == 2:
                out.append((p, 3, 'jump track to $%04x'
                            % (SONGTAB + m.w(p + 1))))
                return out
            n = {1: 1, 4: 3, 5: 1, 6: 1, 7: 2, 8: 4, 9: 6, 10: 1, 11: 4,
                 12: 4, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 1, 19: 1,
                 20: 1}[i]
            txt = {
                1: 'slide down 1 semitone per row',
                4: 'release at row %d, -%d every %d' % (
                    m.b(p + 1), m.b(p + 2) & 15, m.b(p + 2) >> 4),
                5: 'rest', 6: 'note off',
                7: 'transpose %+d' % s8(m.b(p + 1)),
                8: 'vibrato delay %d depth %d speed %d' % (
                    m.b(p + 1), m.b(p + 2), m.b(p + 3)),
                9: 'bend %+d then %+d x%d' % (
                    (m.b(p + 2) << 8) | m.b(p + 1),
                    (m.b(p + 4) << 8) | m.b(p + 3), m.b(p + 5)),
                10: 'bend off',
                11: 'porta %+d x%d' % ((m.b(p + 2) << 8) | m.b(p + 1),
                                       m.b(p + 3)),
                12: 'porta-on-release %+d x%d' % (
                    (m.b(p + 2) << 8) | m.b(p + 1), m.b(p + 3)),
                13: 'porta-on-release off', 14: 'legato on', 15: 'legato off',
                16: 'arp off', 17: 'vol -1', 18: 'vol +1', 19: 'fade out',
                20: 'sfx off',
            }[i]
            out.append((p, n, txt))
            p += n
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('module')
    ap.add_argument('--patterns', action='store_true')
    a = ap.parse_args()
    m = Module(a.module)

    print('== instruments (table $%04x) ==' % INSTAB)
    for i, (s, ln, lp, ll) in enumerate(m.inst):
        if not ln:
            print('%2d  (unused)' % i)
            continue
        loop = 'loop +%d len %d' % (lp - s, ll * 2) if ll * 2 > 32 else 'one-shot'
        print('%2d  $%06x  %6d bytes  %s' % (i, s, ln * 2, loop))

    print()
    print('== arpeggio tables (index $%04x, 9 entries) ==' % ARPTAB)
    for i, t in enumerate(m.arp[:9]):
        seq, p = [], t
        for _ in range(32):
            b = m.b(p)
            p += 1
            seq.append('%+d' % (b & 0x7f))
            if b & 0x80:
                break
        print('%2d  %s  (loop)' % (i, ' '.join(seq)))

    print()
    print('== songs (table $%04x) ==' % SONGTAB)
    for s in range(NSONGS):
        tracks, tempo = m.song[s]
        print('%d  tempo %d (%.1f rows/s)  tracks %s'
              % (s + 1, tempo, 50.0 / tempo,
                 ' '.join('$%04x' % t for t in tracks)))
        for v, t in enumerate(tracks):
            lst, p = [], t
            while True:
                d = m.w(p)
                p += 2
                if d == 0:
                    lst.append('-> $%04x' % (SONGTAB + m.w(p)))
                    break
                if not 0xa8e <= SONGTAB + d < 0x16d4:
                    lst.append('(ends with $83 in the pattern)')
                    break
                lst.append('$%04x' % (SONGTAB + d))
                if len(lst) > 200:
                    break
            print('     v%d: %s' % (v, ' '.join(lst)))

    if not a.patterns:
        return
    pats = set()
    for s in range(NSONGS):
        for t in m.song[s][0]:
            p = t
            for _ in range(256):
                d = m.w(p)
                p += 2
                if d == 0 or not 0xa8e <= SONGTAB + d < 0x16d4:
                    break
                pats.add(SONGTAB + d)
    print()
    print('== %d patterns ==' % len(pats))
    for p in sorted(pats):
        print('$%04x:' % p)
        for (a_, n, txt) in decode_pattern(m, p):
            print('   $%04x  %-14s %s'
                  % (a_, ' '.join('%02x' % m.b(a_ + k) for k in range(n)), txt))


if __name__ == '__main__':
    main()
