"""Render a Paradroid90 (JPO custom) subsong to a WAV file."""
import sys, os, struct, argparse, wave
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jpo import load_cust, Player


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('module')
    ap.add_argument('-s', '--subsong', type=int, default=1)
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('-t', '--seconds', type=float, default=180.0)
    ap.add_argument('-r', '--rate', type=int, default=44100)
    ap.add_argument('--master', type=int, default=255)
    ap.add_argument('--stereo', type=float, default=0.7,
                    help='Amiga LRRL separation, 1.0 = hard panned')
    ap.add_argument('--loops', type=int, default=1,
                    help='stop after the song restarts this many times')
    ap.add_argument('--trace', action='store_true')
    ap.add_argument('--play', action='store_true', help='play the result (Windows)')
    a = ap.parse_args()

    mod = load_cust(a.module)
    pl = Player(mod, master=a.master, out_rate=a.rate)
    if a.trace:
        def tr(fr, v, note):
            print('%7.3fs ch%d inst=%-3d note=%s per=%d' %
                  (fr / pl.rate, v.n, v.instoff // 0x30 + 1,
                   note if note is not None else '--',
                   pl.paula.per[v.n]))
        pl.trace = tr

    pl.pending = a.subsong
    spf = a.rate / pl.rate                      # output samples per replay frame
    nframes = int(a.seconds * pl.rate)
    buf = [[0.0] * (int(spf) + 2) for _ in range(4)]
    left = []
    right = []
    carry = 0.0
    restarts = 0
    for f in range(nframes):
        pl.frame()
        if pl.songend:
            pl.songend = False
            restarts += 1
            if restarts > a.loops:
                break
        carry += spf
        n = int(carry)
        carry -= n
        pl.paula.render(n, buf)
        sep = a.stereo
        for i in range(n):
            l = buf[0][i] + buf[3][i]
            r = buf[1][i] + buf[2][i]
            left.append(l + (1.0 - sep) * r)
            right.append(r + (1.0 - sep) * l)

    peak = max(1.0, max(max(abs(x) for x in left), max(abs(x) for x in right)))
    scale = 32000.0 / peak
    out = a.out or os.path.splitext(a.module)[0] + '_sub%d.wav' % a.subsong
    with wave.open(out, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(a.rate)
        data = bytearray()
        for l, r in zip(left, right):
            data += struct.pack('<hh', int(l * scale), int(r * scale))
        w.writeframes(bytes(data))
    print('%s  %.1fs  peak=%.0f  frames=%d  replay=%.3f Hz' %
          (out, len(left) / a.rate, peak, pl.frames, pl.rate))
    if a.play:
        import winsound
        winsound.PlaySound(out, winsound.SND_FILENAME)


if __name__ == '__main__':
    main()
