"""Render an SN76489 VGM log to a WAV file (BBC Micro clock by default).

Lets you hear the converted tune without a BBC or a VGM player.
"""
import sys, struct, wave, argparse

VOL = [int(32767 / 8 * (10 ** (-2.0 * i / 20.0))) for i in range(15)] + [0]


class SN76489:
    def __init__(self, clock=4000000, rate=44100, fb=0x0009, width=16):
        self.rate = rate
        self.step = (clock / 16.0) / rate        # chip cycles per output sample
        self.tone = [1, 1, 1, 1]
        self.vol = [15] * 4
        self.count = [0.0] * 4
        self.flip = [1] * 4
        self.noise_mode = 0
        self.lfsr = 1 << (width - 1)
        self.fb = fb
        self.width = width
        self.latch = 0

    def write(self, b):
        if b & 0x80:
            self.latch = (b >> 4) & 0x07
            ch = self.latch >> 1
            if self.latch & 1:
                self.vol[ch] = b & 0x0f
            elif ch == 3:
                self.noise_mode = b & 0x07
                self.lfsr = 1 << (self.width - 1)
            else:
                self.tone[ch] = (self.tone[ch] & 0x3f0) | (b & 0x0f)
        else:
            ch = self.latch >> 1
            if self.latch & 1:
                self.vol[ch] = b & 0x0f
            elif ch == 3:
                self.noise_mode = b & 0x07
            else:
                self.tone[ch] = (self.tone[ch] & 0x0f) | ((b & 0x3f) << 4)

    def render(self, n):
        out = []
        for _ in range(n):
            s = 0
            for c in range(3):
                per = self.tone[c] or 1024
                self.count[c] += self.step
                while self.count[c] >= per:
                    self.count[c] -= per
                    self.flip[c] = -self.flip[c]
                s += VOL[self.vol[c]] * (1 if self.flip[c] > 0 else -1)
            nm = self.noise_mode & 3
            nper = (self.tone[2] or 1024) if nm == 3 else (16 << nm)
            self.count[3] += self.step
            while self.count[3] >= nper:
                self.count[3] -= nper
                bit = self.lfsr & 1
                if self.noise_mode & 4:
                    fbv = self.lfsr & self.fb
                    par = 0
                    while fbv:
                        par ^= fbv & 1
                        fbv >>= 1
                else:
                    par = bit
                self.lfsr = (self.lfsr >> 1) | (par << (self.width - 1))
                self.flip[3] = 1 if (self.lfsr & 1) else -1
            s += VOL[self.vol[3]] * self.flip[3]
            out.append(max(-32768, min(32767, s)))
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('vgm')
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('-r', '--rate', type=int, default=44100)
    ap.add_argument('-t', '--seconds', type=float, default=1e9)
    a = ap.parse_args()

    d = open(a.vgm, 'rb').read()
    assert d[:4] == b'Vgm ', 'not a VGM file'
    clock = struct.unpack_from('<I', d, 0x0c)[0] or 3579545
    ver = struct.unpack_from('<I', d, 0x08)[0]
    off = 0x40 if ver < 0x150 else 0x34 + struct.unpack_from('<I', d, 0x34)[0]
    fb = struct.unpack_from('<H', d, 0x28)[0] or 0x0009
    width = d[0x2a] or 16

    chip = SN76489(clock, a.rate, fb, width)
    samples = []
    p = off
    limit = a.seconds * a.rate
    while p < len(d) and len(samples) < limit:
        c = d[p]
        p += 1
        if c == 0x50:
            chip.write(d[p]); p += 1
        elif c == 0x61:
            n = struct.unpack_from('<H', d, p)[0]; p += 2
            samples += chip.render(int(n * a.rate / 44100))
        elif c == 0x62:
            samples += chip.render(int(a.rate / 60))
        elif c == 0x63:
            samples += chip.render(int(a.rate / 50))
        elif c == 0x66:
            break
        elif 0x70 <= c <= 0x7f:
            samples += chip.render(int((c - 0x6f) * a.rate / 44100))
        else:
            raise SystemExit('unhandled VGM command %02x at %x' % (c, p - 1))

    out = a.out or a.vgm.rsplit('.', 1)[0] + '_sn.wav'
    with wave.open(out, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(a.rate)
        w.writeframes(struct.pack('<%dh' % len(samples), *samples))
    print('%s  %.1fs  clock=%d Hz' % (out, len(samples) / a.rate, clock))


if __name__ == '__main__':
    main()
