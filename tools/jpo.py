"""
Jason Page "JPO" replayer -- reimplementation of the 68k driver found in
cust.Paradroid_90 (DeliTracker Custom; Jason Page, 1990, Hewson/Graftgold).

Faithful re-implementation of the routines at (addresses in the flattened,
relocated hunk image):
  $442 frame interrupt, $3aa master fade, $4c6 song start,
  $524/$586/$63c sequencer, $6c2.. volume envelope, $826 volume output,
  $850/$8a2/$944 pitch envelope + vibrato, $98e/$9be portamento,
  $9de/$9f0 noise.
"""
import struct

PAL_CLK = 3546895.0          # Paula sample clock (PAL)
CIA_CLK = 709379.0           # CIA-B clock (PAL)
RTS = None                   # "routine pointer = rts"


# --------------------------------------------------------------- module ----
class Module:
    def __init__(self, img, songs, seqtab, instrs, wavetab, noise, silence,
                 pertab, timer, nsongs):
        self.img = img
        self.songs = songs
        self.seqtab = seqtab
        self.instrs = instrs
        self.wavetab = wavetab
        self.noise = noise
        self.silence = silence
        self.pertab = pertab
        self.timer = timer
        self.nsongs = nsongs

    def b(self, a):
        return self.img[a]

    def sb(self, a):
        v = self.img[a]
        return v - 256 if v > 127 else v

    def w(self, a):
        return struct.unpack_from('>H', self.img, a)[0]

    def l(self, a):
        return struct.unpack_from('>I', self.img, a)[0]


def load_cust(path):
    """Load a DeliTracker-custom Paradroid90 module and locate its tables."""
    from hunk import parse
    hs = parse(path)
    base = 0
    for h in hs:
        h['off'] = base
        base += len(h['data'])
    img = bytearray()
    for h in hs:
        img += h['data']
    for h in hs:
        for (ofs, tgt) in h['reloc']:
            p = h['off'] + ofs
            v = struct.unpack_from('>I', img, p)[0] + hs[tgt]['off']
            struct.pack_into('>I', img, p, v)
    img = bytes(img)

    # InitSound writes the four root pointers: move.l #ptr,$xx(a5)
    ptrs = {}
    i = img.find(b'\x2b\x7c')
    while i > 0 and len(ptrs) < 4:
        val = struct.unpack_from('>I', img, i + 2)[0]
        off = struct.unpack_from('>H', img, i + 6)[0]
        if off in (0, 4, 8, 0xc) and val < len(img):
            ptrs[off] = val
        i = img.find(b'\x2b\x7c', i + 8)
    instrs, songs, seqtab, wavetab = ptrs[8], ptrs[0], ptrs[4], ptrs[0xc]

    # a5 = player context: first "lea $xxx(pc),a5"
    j = img.find(b'\x4b\xfa')
    a5 = j + 2 + struct.unpack_from('>h', img, j + 2)[0]
    noise = struct.unpack_from('>I', img, a5 + 0x10)[0]
    silence = noise - 0xc                    # 'SS' header word of that sample

    # period table: "lea $1ee(pc),a0" in the note handler
    # period table: lea $xxx(pc),a0 / add.w d0,d0 / move.w (a0,d0.w),d0
    k = img.find(b'\xd0\x40\x30\x30\x00\x00') - 4
    assert img[k:k + 2] == b'\x41\xfa'
    pertab = k + 2 + struct.unpack_from('>h', img, k + 2)[0]

    nsongs = 5
    m = img.find(b'\x70\x01\x72')
    if m > 0:
        nsongs = img[m + 3]

    t = img.find(b'\x3b\x7c')
    timer = struct.unpack_from('>H', img, t + 2)[0] if t > 0 else 0x1bc0

    return Module(img, songs, seqtab, instrs, wavetab, noise, silence,
                  pertab, timer, nsongs)


# ---------------------------------------------------------------- Paula ----
class Paula:
    """Behaviourally faithful 4-channel Paula with box-filtered resampling."""

    def __init__(self, mem, out_rate=44100):
        self.mem = mem
        self.rate = out_rate
        self.lc = [0] * 4
        self.ln = [1] * 4
        self.per = [1000] * 4
        self.vol = [0] * 4
        self.dma = [False] * 4
        self.ptr = [0] * 4
        self.cnt = [0] * 4
        self.cur = [0] * 4
        self.acc = [0.0] * 4

    def dmacon(self, val):
        on = val & 0x8000
        for c in range(4):
            if val & (1 << c):
                if on:
                    if not self.dma[c]:
                        self.ptr[c] = self.lc[c]
                        self.cnt[c] = self.ln[c]
                        self.acc[c] = 0.0
                        self.dma[c] = True
                        self._fetch(c)
                    self.dma[c] = True
                else:
                    self.dma[c] = False

    def _fetch(self, c):
        p = self.ptr[c]
        mem = self.mem
        self.cur[c] = mem[p] - 256 if mem[p] > 127 else mem[p]
        p += 1
        # Paula fetches a word per DMA slot; the length counter counts words.
        if ((p - self.lc[c]) & 1) == 0:
            self.cnt[c] -= 1
            if self.cnt[c] <= 0:
                p = self.lc[c]
                self.cnt[c] = self.ln[c]
        self.ptr[c] = p

    def render(self, n, buf):
        for c in range(4):
            out = buf[c]
            if not self.dma[c]:
                for i in range(n):
                    out[i] = 0.0
                continue
            step = PAL_CLK / max(self.per[c], 1) / self.rate
            v = self.vol[c]
            a = self.acc[c]
            cur = self.cur
            for i in range(n):
                rem = step
                tot = 0.0
                while True:
                    avail = 1.0 - a
                    if rem < avail:
                        tot += cur[c] * rem
                        a += rem
                        break
                    tot += cur[c] * avail
                    rem -= avail
                    a = 0.0
                    self._fetch(c)
                out[i] = tot / step * v
            self.acc[c] = a


# --------------------------------------------------------------- voice -----
def s8(v):
    return v - 256 if v > 127 else v


def w16(v):
    return v & 0xffff


def sw16(v):
    v &= 0xffff
    return v - 0x10000 if v > 0x7fff else v


class Voice:
    def __init__(self, n):
        self.n = n
        self.dmabit = 1 << n
        self.volreg = 0xa8 + n * 0x10
        self.perreg = 0xa6 + n * 0x10
        self.lcreg = 0xa0 + n * 0x10
        self.lenreg = 0xa4 + n * 0x10
        self.val = [0, 0, 0]
        self.base = [0, 0, 0]
        self.delta = [0, 0, 0]
        self.inner = [0, 0, 0]
        self.trkptr = 0
        self.patptr = 0
        self.seqstate = RTS
        self.inst = 0
        self.reset()

    def reset(self):
        """$374 -- move.l #$0000ffff into +$1a/+$1e/+$22 leaves prio ($23) = 0"""
        self.prio = 0
        self.instno = 0
        self.instoff = 0xffff
        self.envstate = RTS
        self.pitchstate = RTS
        self.portstate = RTS
        self.noisestate = RTS
        self.envcnt = 0
        self.envdelta = 0
        self.envrep = 0
        self.susrep = 0
        self.vol = 0
        self.arpd4 = 0
        self.arpd3 = 0
        self.arpstep = 1
        self.arpinner = 0
        self.arpouter = 0
        self.arptick = 0
        self.vibdel = 0
        self.vibcnt = 0
        self.vibpos = 0
        self.dur = 0
        self.durcnt = 0
        self.portdst = 0
        self.portspd = 0
        self.pendinst = 0
        self.pendprio = 0
        self.instzero = 0
        for i in range(3):
            self.val[i] = self.base[i] = self.delta[i] = self.inner[i] = 0


# -------------------------------------------------------------- player -----
class Player:
    def __init__(self, mod, master=255, out_rate=44100, seed=0x1234):
        self.m = mod
        self.paula = Paula(mod.img, out_rate)
        self.voices = [Voice(i) for i in range(4)]
        self.master = master
        self.speed = 1
        self.speedctr = 1
        self.songprio = 0
        self.songno = 0
        self.nextsong = 0
        self.pending = 0
        self.rate = CIA_CLK / mod.timer
        self.songend = False
        self.frames = 0
        self.trace = None
        # $a14 random table: 128 words, d0 = (d0*$ab) mod $7673 + 1
        self.rng = []
        d0 = seed
        for _ in range(128):
            d0 = (d0 * 0xab) % 0x7673 + 1
            self.rng.append(d0)
        self.rngpos = 0

    # ---- custom chip write ---------------------------------------------
    def hw(self, reg, val):
        p = self.paula
        if reg == 0x96:
            p.dmacon(val)
            return
        c = (reg - 0xa0) // 0x10
        r = reg & 0x0f
        if r == 0x00:
            p.lc[c] = val
        elif r == 0x04:
            p.ln[c] = val if val else 65536
        elif r == 0x06:
            # Paula cannot fetch faster than one word per two DMA slots;
            # anything below ~124 plays at the same maximum rate.
            p.per[c] = max(val & 0x7ff, 124)
        elif r == 0x08:
            p.vol[c] = min(val, 64)

    # ---- $4c6 : start a (sub)song ---------------------------------------
    def start_song(self, num):
        m = self.m
        a1 = m.songs + ((num & 0x7f) - 1) * 12
        prio = m.b(a1)
        if prio < self.songprio:
            return
        self.songno = num
        self.songprio = prio
        self.speed = m.b(a1 + 1)
        self.speedctr = self.speed
        self.nextsong = m.b(a1 + 2)
        self.songend = False
        for i, v in enumerate(self.voices):
            off = m.w(a1 + 4 + i * 2)
            if off:
                v.trkptr = m.songs + off
                v.reset()
                self.hw(0x96, v.dmabit)
                v.seqstate = 'trk'

    # ---- $656 : trigger an instrument ------------------------------------
    def note_on(self, v, instoff, prio):
        m = self.m
        v.instoff = instoff
        v.prio = prio
        v.instno = instoff // 0x30 + 1
        v.inst = m.instrs + instoff
        v.envstate = 'e0'
        v.pitchstate = RTS
        v.portstate = RTS
        v.noisestate = RTS
        wf = m.sb(v.inst + 0x1f)
        if wf < 0:
            self.hw(v.lenreg, 0x100)         # $9de
            v.noisestate = 'noise'
            self.noise_tick(v)
        else:
            rel = struct.unpack_from('>i', m.img, m.wavetab + wf * 4)[0]
            a0 = m.wavetab + rel
            self.hw(v.lenreg, m.w(a0) >> 1)
            self.hw(v.lcreg, a0 + 2)
        self.pitch_init(v)

    def rand(self):
        self.rngpos = (self.rngpos + 1) & 0x7f
        return self.rng[self.rngpos]

    def noise_tick(self, v):
        self.hw(v.lcreg, self.m.noise + (self.rand() & 0x1fe))

    # ---- $850 / $8a2 : pitch (arpeggio) envelope -------------------------
    def pitch_init(self, v):
        m = self.m
        ins = v.inst
        v.vibdel = m.b(ins + 0x2c)
        v.vibpos = 0
        v.arpstep = 1
        v.arpd4 = 0
        v.arpd3 = 0
        for i in range(min(m.b(ins + 0x2b) + 1, 3)):
            e = ins + i * 10
            val = m.w(e)
            v.val[i] = val
            v.base[i] = val
            v.delta[i] = m.w(e + 2)
            v.inner[i] = m.b(e + 5)
        v.pitchstate = 'p0'

    def pitch_tick(self, v):
        m = self.m
        ins = v.inst
        while True:
            st = v.pitchstate
            if st is RTS:
                return
            if st == 'p0':                                  # $8a2
                v.arpouter = m.b(ins + v.arpd3 + 4)
                v.pitchstate = 'p1'
                continue
            if st == 'p1':                                  # $8a8
                v.arpinner = v.inner[v.arpd4 >> 3]
                v.pitchstate = 'p2'
                continue
            if st == 'p2':                                  # $8ae
                v.arptick = m.b(ins + v.arpd3 + 6)
                v.pitchstate = 'p3'
                self.vib_out(v)
                v.arptick = (v.arptick - 1) & 0xff
                if v.arptick == 0:
                    v.pitchstate = 'p4'
                return
            if st == 'p3':                                  # $8bc
                self.vib_out(v)
                v.arptick = (v.arptick - 1) & 0xff
                if v.arptick == 0:
                    v.pitchstate = 'p4'
                return
            if st == 'p4':                                  # $8d2
                s = v.arpd4 >> 3
                v.val[s] = w16(v.val[s] + v.delta[s])
                v.delta[s] = w16(v.delta[s] + m.sb(ins + v.arpd3 + 7))
                v.arpinner = (v.arpinner - 1) & 0xff
                if v.arpinner != 0:
                    v.pitchstate = 'p2'
                    continue
                f = m.b(ins + v.arpd3 + 8)
                if f & 1:
                    v.val[s] = v.base[s]
                if f & 2:
                    v.delta[s] = w16(-v.delta[s])
                if (f & 4) and v.inner[s] > 8:
                    v.inner[s] -= 1
                v.arpouter = (v.arpouter - 1) & 0xff
                if v.arpouter != 0:
                    v.pitchstate = 'p1'
                    continue
                if v.arpstep == m.b(ins + 0x2b):
                    v.arpstep = 1
                    v.arpd4 = 0
                    v.arpd3 = 0
                else:
                    v.arpstep += 1
                    v.arpd4 += 8
                    v.arpd3 += 10
                v.pitchstate = 'p0'
                continue
            raise AssertionError(st)

    # ---- $944 : vibrato + AUDxPER ----------------------------------------
    def vib_out(self, v):
        m = self.m
        ins = v.inst
        s = v.arpd4 >> 3
        spd = m.b(ins + 0x2d)
        if spd:
            if v.vibdel:
                v.vibdel -= 1
            else:
                d0 = s8(v.vibpos)
                v.val[s] = w16(v.val[s] + d0)
                v.vibcnt = (v.vibcnt - 1) & 0xff
                if v.vibcnt == 0:
                    v.vibcnt = spd
                    d0 = (-d0) & 0xff
                    if not (d0 & 0x80) and d0 < m.sb(ins + 0x2e):
                        d0 = (d0 + 1) & 0xff
                    v.vibpos = d0
        self.hw(v.perreg, (v.val[s] >> m.b(ins + 0x25)) & 0xffff)

    # ---- $98e / $9be : portamento ----------------------------------------
    def set_note(self, v, per):
        if v.portspd:
            v.val[0] = w16(v.portdst)
            v.portdst = per
            v.portstate = 'port'
            v.base[0] = per
        else:
            v.portdst = per
            v.val[0] = per
            v.base[0] = per
            v.portstate = RTS

    def port_tick(self, v):
        d0 = sw16(v.portdst - v.val[0]) >> v.portspd
        if d0 == 0:
            v.val[0] = v.portdst
            v.portstate = RTS
        else:
            v.val[0] = w16(v.val[0] + d0)

    # ---- $826 : volume ramp + AUDxVOL ------------------------------------
    def vol_out(self, v):
        d0 = s8(v.envdelta) + v.vol
        if d0 < 0:
            d0 = 0
        if d0 > self.master:
            d0 = self.master
        v.vol = d0
        self.hw(v.volreg, d0 >> 3)

    # ---- $6c2.. : volume envelope ----------------------------------------
    def env_tick(self, v):
        m = self.m
        ins = v.inst
        while True:
            st = v.envstate
            if st is RTS:
                return
            if st == 'e0':                                  # $6c2
                self.hw(0x96, v.dmabit)
                v.envstate = 'e1'
                return
            if st == 'e1':                                  # $6d2
                self.hw(v.volreg, 0)
                self.hw(0x96, 0x8000 | v.dmabit)
                d0 = m.b(ins + 0x20)
                v.envrep = d0
                v.instzero = d0
                if d0 == 0:
                    v.vol = self.master
                    self.hw(v.volreg, self.master >> 3)
                    v.envrep = 1
                else:
                    v.vol = 0
                v.envcnt = m.b(ins + 0x21)
                v.envdelta = m.b(ins + 0x22)
                v.envstate = 'e2'
                return
            if st == 'e2':                                  # $72a
                if v.instzero == 0:
                    self.hw(v.lenreg, 1)
                    self.hw(v.lcreg, m.silence)
                v.envstate = 'e3'
                continue                                    # $746 falls into $74e
            if st == 'e3':                                  # $74e  attack
                self.vol_out(v)
                v.envcnt = (v.envcnt - 1) & 0xffff
                if v.envcnt:
                    return
                v.envcnt = m.b(ins + 0x23)
                v.envdelta = m.b(ins + 0x24)
                v.envstate = 'e4'
                return
            if st == 'e4':                                  # $770  decay
                self.vol_out(v)
                v.envcnt = (v.envcnt - 1) & 0xffff
                if v.envcnt:
                    return
                v.envcnt = m.w(ins + 0x26)
                v.envdelta = (-s8(m.b(ins + 0x28))) & 0xff
                v.susrep = m.b(ins + 0x29)
                v.envstate = 'e5'
                return
            if st == 'e5':                                  # $79c  sustain
                self.vol_out(v)
                v.envcnt = (v.envcnt - 1) & 0xffff
                if v.envcnt == 0:
                    v.envdelta = m.b(ins + 0x2a)
                    v.envstate = 'e6'
                    return
                v.susrep = (v.susrep - 1) & 0xff
                if v.susrep == 0:
                    v.susrep = m.b(ins + 0x29)
                    v.envdelta = (-s8(v.envdelta)) & 0xff
                return
            if st == 'e6':                                  # $7be  release
                self.vol_out(v)
                if v.vol != 0:
                    return
                v.envrep = (v.envrep - 1) & 0xff
                if v.envrep != 0:
                    v.vol = 0
                    v.envcnt = m.b(ins + 0x21)
                    v.envdelta = m.b(ins + 0x22)
                    v.envstate = 'e2'
                    return
                nxt = m.b(ins + 0x2f)
                if nxt:
                    off = (nxt - 1) * 0x30
                    self.note_on(v, off, m.b(m.instrs + off + 0x1e))
                    return
                self.hw(v.volreg, 0)
                self.hw(0x96, v.dmabit)
                v.envstate = RTS
                v.pitchstate = RTS
                v.portstate = RTS
                v.noisestate = RTS
                v.instoff = 0xffff
                v.instno = 0
                v.prio = 0
                return
            raise AssertionError(st)

    # ---- $524 / $586 / $63c : the sequencer ------------------------------
    def seq_tick(self, v):
        m = self.m
        while True:
            st = v.seqstate
            if st is RTS:
                return
            if st == 'trk':                                 # $524
                d0 = m.b(v.trkptr)
                v.trkptr += 1
                if d0 < 0xfe:
                    v.patptr = m.seqtab + m.w(m.seqtab + d0 * 2)
                    v.seqstate = 'pat'
                    continue
                if d0 == 0xff:
                    self.song_end()
                    return
                v.seqstate = RTS
                return
            if st == 'pat':                                 # $586
                a0 = v.patptr
                nxt = 'wait'
                while True:
                    d0 = m.b(a0)
                    a0 += 1
                    if d0 < 0x80:                                # note
                        v.patptr = a0
                        if v.pendprio >= v.prio:
                            self.note_on(v, v.pendinst, v.pendprio)
                            self.set_note(v, m.w(m.pertab + d0 * 2))
                            if self.trace is not None:
                                self.trace(self.frames, v, d0)
                        break
                    if d0 < 0xb0:                                # $80..$af
                        v.dur = (d0 - 0x7f) & 0xff
                        continue
                    if d0 < 0xd0:                                # $b0..$cf
                        v.patptr = a0
                        off = (d0 - 0xb0) * 0x30
                        prio = m.b(m.instrs + off + 0x1e)
                        if prio >= v.prio:
                            self.note_on(v, off, prio)
                            if self.trace is not None:
                                self.trace(self.frames, v, None)
                        break
                    if d0 < 0xf0:                                # $d0..$ef
                        off = (d0 - 0xd0) * 0x30
                        v.pendinst = off
                        v.pendprio = m.b(m.instrs + off + 0x1e)
                        continue
                    if d0 < 0xf9:                                # $f0..$f8
                        v.portspd = d0 - 0xf0
                        continue
                    if d0 == 0xfe:                               # rest
                        v.patptr = a0
                        break
                    nxt = 'trk'                                  # end of pattern
                    break
                if nxt == 'trk':
                    v.seqstate = 'trk'
                    continue
                v.durcnt = v.dur
                v.seqstate = 'wait'
                return
            if st == 'wait':                                # $63c
                if self.speedctr != 0:
                    if v.durcnt == 0:
                        v.seqstate = 'pat'
                        continue
                    return
                v.durcnt = (v.durcnt - 1) & 0xff
                if v.durcnt == 0:
                    v.seqstate = 'pat'
                    continue
                return
            raise AssertionError(st)

    def song_end(self):
        """$546"""
        for v in self.voices:
            v.seqstate = RTS
        self.songprio = 0
        self.songend = True
        if not (self.songno & 0x80) and self.nextsong:
            self.pending = self.nextsong
        self.songno = 0

    # ---- $3aa + $442 : one replay interrupt ------------------------------
    def frame(self):
        self.frames += 1
        if self.pending:
            self.start_song(self.pending)
            self.pending = 0
        self.speedctr = (self.speedctr - 1) & 0xff
        for v in self.voices:
            self.seq_tick(v)
            self.env_tick(v)
            self.pitch_tick(v)
            if v.portstate == 'port':
                self.port_tick(v)
            if v.noisestate == 'noise':
                self.noise_tick(v)
        if self.speedctr == 0:
            self.speedctr = self.speed
