"""
Tony Williams / "Tiny" Amiga music driver ("TW" format) replayer.

Reimplementation of the 68k driver embedded in tw.Nitro (Nitro, 1990).
The module is a raw, position-independent 68k blob -- code, tables, sequence
data and samples in one file, entered by calling offset 0 once per video
frame (50 Hz).  Addresses below are file offsets in that blob.

  $000 bra to the frame routine        $09c per-frame entry
  $01a level-4 audio interrupt (one-shot -> loop repointing)
  $112 volume table (15 entries)       $130 SFX voice update
  $152 music voice update              $1c4 sequencer / note-on
  $276 command dispatch                $2e4 $80..$9f jump table
  $30e $a0..$af arpeggio table index   $394 SFX off
  $3a6 note slide  $3c4 ADSR  $432 release trigger
  $474 portamento  $4d6 vibrato  $61e master fade
  $64e period table (36 ProTracker periods)
  $696 arpeggio + period write         $6be note -> period
  $6f4 instrument select               $71e instrument table (13 x 12 bytes)
  $7ba voice contexts (5 x $48)        $932 song start
  $9d2 song table (6 x 10) then track lists and patterns
  $1576 SFX trigger   $16d4 zeroed 32-byte loop buffer   $16fc.. samples

The driver's own data offsets are relative to a5, which is the load address
plus 4; PC-relative ones are not.  A5REL below is that fudge.
"""
import struct, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jpo import Paula, PAL_CLK                    # same Paula emulation

A5REL = 4
VBL = 50.0

VOLTAB = 0x112          # 15 words
PERTAB = 0x64e          # 36 words, 856..113
INSTAB = 0x71e          # 13 x 12 bytes
CMDTAB = 0x2e4          # 21 words, $80..$94
ARPTAB = 0x30e          # 16 words
SONGTAB = 0x9d2         # 6 x 10 bytes, also the base for all data offsets
VOICES = (0x7ba, 0x802, 0x85a, 0x8a2)
PWMBUF = 0x16d4
NSONGS = 6


def s8(v):
    return v - 256 if v > 127 else v


class Module:
    def __init__(self, path):
        self.img = open(path, 'rb').read()
        self.per = [self.w(PERTAB + 2 * i) for i in range(36)]
        self.vol = [self.w(VOLTAB + 2 * i) for i in range(15)]
        self.inst = []
        for i in range(13):
            a = INSTAB + i * 12
            self.inst.append((self.l(a) + A5REL, self.w(a + 4),
                              self.l(a + 6) + A5REL, self.w(a + 10)))
        self.arp = [ARPTAB + self.w(ARPTAB + 2 * i) for i in range(16)]
        self.song = []
        for s in range(NSONGS):
            a = SONGTAB + s * 10
            self.song.append(([SONGTAB + self.w(a + 2 * v) for v in range(4)],
                              self.img[a + 8]))

    def b(self, a):
        return self.img[a]

    def w(self, a):
        return struct.unpack_from('>H', self.img, a)[0]

    def l(self, a):
        return struct.unpack_from('>I', self.img, a)[0]


class Voice:
    def __init__(self, n):
        self.n = n
        self.dmabit = 0x8200 | (1 << n)
        self.smp = self.slen = self.loop = self.looplen = 0
        self.period = 0
        self.volume = 0            # +09 live volume 0..15
        self.trk = self.pat = 0    # +0e track pointer, +12 pattern pointer
        self.flags = 0             # +16
        self.dur = 1               # +17 rows left on this note
        self.note = 0              # +18
        self.slide = 0             # +19 semitones added per row
        self.setvol = 0            # +24
        self.atkstep = self.atkn = 0          # +25 +26
        self.decstep = self.decn = 0          # +27 +28
        self.relstep = 0                      # +29
        self.reltrig = 0                      # +2a
        self.rel = 0                          # +2b
        self.atkcnt = self.deccnt = 0         # +2c +2d
        self.vibdepth = self.vibspd = 0       # +2e +2f
        self.vibcnt = 0                       # +30
        self.pcnt = 0                         # +31
        self.pstep = 0                        # +32 word, bit15 = negative
        self.bend = 0                         # +34 word added at note-on
        self.bendcnt = 0                      # +36
        self.defdur = 1                       # +37
        self.atkspd = self.atkspd_rl = 1      # +3a +3b
        self.decspd = self.decspd_rl = 1      # +3c +3d
        self.relspd = self.relspd_rl = 1      # +3e +3f
        self.vibdelay = 0                     # +40
        self.arpidx = 0                       # +41
        self.arptab = 0                       # +42
        self.transpose = 0                    # +46
        self.inst = -1


class Player:
    def __init__(self, mod, out_rate=44100, master=0x10):
        self.m = mod
        self.rate = VBL
        self.paula = Paula(mod.img, out_rate)
        self.v = [Voice(i) for i in range(4)]
        self.song = 0
        self.master0 = master
        self.master = master
        self.fadespd = self.fadecnt = 0
        self.pend_dma = 0
        self.tempo = 6
        self.tick = 1
        self.req = 0
        self.frames = 0
        self.songend = False
        self.trace = None
        self.loopcount = 0
        # emulate the level-4 handler: repoint to the loop at the first wrap
        self._install_loop_hook()

    def _install_loop_hook(self):
        p = self.paula
        orig = p._fetch
        vs = self.v

        def fetch(c):
            before = p.cnt[c]
            orig(c)
            if p.cnt[c] > before:                      # wrapped
                p.lc[c] = vs[c].loop
                p.ln[c] = vs[c].looplen
                p.ptr[c] = p.lc[c]
                p.cnt[c] = p.ln[c]
        p._fetch = fetch

    # ------------------------------------------------------------ frame ---
    def frame(self):
        self.frames += 1
        if self.pend_dma:
            self.paula.dmacon(self.pend_dma)
        self.pend_dma = 0
        self.newsong()
        self.isrow = False
        if self.song:
            self.tick = (self.tick - 1) & 0xff
            self.isrow = self.tick == 0
            for v in self.v:
                self.voice(v)
            self.fade()
            if self.tick == 0:
                self.tick = self.tempo

    def newsong(self):                                          # $932
        if not self.req:
            return
        d = self.req
        if d > NSONGS:
            return self.stop()
        self.song = d
        self.paula.dmacon(0x000f)
        self.tick = 1
        self.fadespd = 0
        self.req = 0
        tracks, tempo = self.m.song[d - 1]
        for i, v in enumerate(self.v):
            a2 = tracks[i]
            v.trk = a2 + 2
            v.pat = SONGTAB + self.m.w(a2)
            v.flags = 0
            v.vibdepth = 0
            v.transpose = 0
            v.dur = 1
        self.tempo = tempo
        self.master = self.master0

    def stop(self):                                             # $636
        self.paula.dmacon(0x000f)
        self.song = 0
        self.req = 0
        self.fadespd = self.fadecnt = 0
        self.master = self.master0
        self.songend = True

    def fade(self):                                             # $61e
        if not self.fadespd:
            return
        self.fadecnt = (self.fadecnt - 1) & 0xff
        if self.fadecnt:
            return
        self.fadecnt = self.fadespd
        self.master -= 1
        if self.master:
            return
        self.stop()

    def rowstate(self):
        """Sequencer state at a row boundary; repeats when the song loops."""
        return tuple((v.trk, v.pat, v.dur, v.inst, v.setvol, v.note)
                     for v in self.v)

    def volout(self, vol):
        """$162: AUDxVOL = voltab[(vol * master) >> 4].

        The table only holds 15 entries, so the driver reads its own code as
        data for index 15 and up; Paula then takes the low 7 bits.  Levels
        above 14 do occur in the data, so reproduce it rather than clamp.
        """
        idx = (vol * self.master) >> 4
        if idx < 0:
            idx = 0
        return min(self.m.w(VOLTAB + 2 * min(idx, 64)) & 0x7f, 64)

    # ------------------------------------------------------------ voice ---
    def voice(self, v):                                         # $152
        c = v.n
        self.paula.per[c] = max(v.period & 0xffff, 124)
        self.paula.vol[c] = self.volout(v.volume)
        if self.tick == 0:
            v.dur = (v.dur - 1) & 0xff
            if v.dur == 0:
                self.newnote(v)
                return
            self.noteslide(v)
            self.relcheck(v)
        self.envelope(v)
        if v.flags & 0x02:
            self.porta(v)
        if v.flags & 0x04:
            self.porta(v)
        self.arpeggio(v)
        self.vibrato(v)

    def setperiod(self, v, d0):                                 # $6be
        d0 = (d0 + v.transpose) & 0xff
        v.period = self.m.per[((d0 * 2) & 0x7f) >> 1]
        if v.flags & 0x04:
            v.period = (v.period + v.bend) & 0xffff
            v.pcnt = v.bendcnt
        if v.flags & 0x20:
            v.flags &= ~0x02

    def noteslide(self, v):                                     # $3a6
        if not v.slide:
            return
        v.note = (v.note + v.slide) & 0xff
        self.setperiod(v, v.note)
        self.paula.lc[v.n] = v.smp
        self.paula.ln[v.n] = v.slen

    def relcheck(self, v):                                      # $432
        if v.reltrig != v.dur or self.m.b(v.pat) == 0x85:
            return
        v.rel = 1
        v.slide = 0
        v.flags &= ~0x02
        if v.flags & 0x20:
            v.flags |= 0x02

    def envelope(self, v):                                      # $3c4
        if v.atkcnt:
            v.atkspd = (v.atkspd - 1) & 0xff
            if v.atkspd:
                return
            v.atkspd = v.atkspd_rl
            v.atkcnt = (v.atkcnt - 1) & 0xff
            v.volume = (v.volume + v.atkstep) & 0xff
            return
        if v.deccnt:
            v.decspd = (v.decspd - 1) & 0xff
            if v.decspd:
                return
            v.decspd = v.decspd_rl
            v.deccnt = (v.deccnt - 1) & 0xff
            v.volume = max(0, v.volume - v.decstep)
            return
        if v.rel:
            v.relspd = (v.relspd - 1) & 0xff
            if v.relspd:
                return
            v.relspd = v.relspd_rl
            v.volume = max(0, v.volume - v.relstep)

    def porta(self, v):                                         # $474
        if not v.pcnt:
            return
        v.pcnt = (v.pcnt - 1) & 0xff
        d = v.pstep
        if d & 0x8000:
            v.period = (v.period - (d & 0x7fff)) & 0xffff
        else:
            v.period = (v.period + d) & 0xffff

    def arpeggio(self, v):                                      # $696
        if not (v.flags & 0x01):
            return
        d0 = self.m.b(v.arptab + v.arpidx)
        v.arpidx = (v.arpidx + 1) & 0xff
        if d0 & 0x80:
            v.arpidx = 0
            d0 &= 0x7f
        self.setperiod(v, (d0 + v.note) & 0xff)

    def vibrato(self, v):                                       # $4d6
        if self.m.b(v.pat - 1) != 0x85:
            if ((v.defdur - v.dur) & 0xff) < v.vibdelay:
                return
        d = v.vibdepth
        if not d:
            return
        if v.flags & 0x08:
            v.period = (v.period + d) & 0xffff
            v.vibcnt = (v.vibcnt - 1) & 0xff
            if v.vibcnt == 0:
                v.vibcnt = (v.vibspd * 2) & 0xff
                v.flags &= ~0x08
        else:
            v.period = (v.period - d) & 0xffff
            v.vibcnt = (v.vibcnt - 1) & 0xff
            if v.vibcnt == 0:
                v.vibcnt = (v.vibspd * 2) & 0xff
                v.flags |= 0x08

    # -------------------------------------------------------- sequencer ---
    def newnote(self, v):                                       # $1c4
        m = self.m
        p = v.pat
        for _ in range(4096):
            b = m.b(p)
            p += 1
            if b < 0x80:
                v.note = b
                self.setperiod(v, b)
                if not (v.flags & 0x10):
                    self.paula.lc[v.n] = v.smp
                    self.paula.ln[v.n] = v.slen
                    self.pend_dma |= v.dmabit
                    self.paula.dmacon(v.dmabit & 0x0f)
                    v.volume = v.setvol
                    v.atkcnt = v.atkn
                    v.deccnt = v.decn
                    v.rel = 0
                if self.trace:
                    self.trace(self, v, b)
                v.vibcnt = v.vibspd
                v.dur = v.defdur
                v.pat = p
                return
            if b >= 0xe0:
                v.defdur = b - 0xdf
            elif b >= 0xd0:
                v.inst = (b - 0xd0) % 13
                v.smp, v.slen, v.loop, v.looplen = m.inst[v.inst]
            elif b >= 0xc0:
                v.setvol = b - 0xc0
                n1, n2, n3 = m.b(p), m.b(p + 1), m.b(p + 2)
                p += 3
                v.atkstep, v.atkn = n1 >> 4, n1 & 0xf
                v.decstep, v.decn = n2 >> 4, n2 & 0xf
                v.atkspd = v.atkspd_rl = n3 >> 4
                v.decspd = v.decspd_rl = n3 & 0xf
            elif b >= 0xb0:
                v.setvol = b - 0xb0
            elif b >= 0xa0:
                v.arptab = m.arp[b - 0xa0]
                v.arpidx = 0
                v.flags |= 0x01
            else:
                r = self.command(v, b & 0x1f, p)
                if r is None:
                    return
                p = r
        raise RuntimeError('runaway pattern on voice %d' % v.n)

    def command(self, v, idx, p):
        """Return the new pattern pointer, or None if the row is finished."""
        m = self.m
        if idx == 0 or idx == 2:                       # $80 next / $82 jump
            if idx == 2:
                v.trk = SONGTAB + m.w(p)
            v.flags &= ~0x01
            while True:
                d0 = m.w(v.trk)
                v.trk += 2
                if d0:
                    break
                # end of track list: the word after the terminator is the
                # track-list offset to resume from ($550 -> $568 -> $534)
                v.trk = SONGTAB + m.w(v.trk)
                self.loopcount += 1
            v.pat = SONGTAB + d0
            return v.pat
        if idx == 1:                                   # $81 slide down
            v.slide = -1
            return p
        if idx == 3:                                   # $83 stop
            self.stop()
            return None
        if idx == 4:                                   # $84 release params
            v.reltrig = m.b(p)
            n = m.b(p + 1)
            v.relspd = v.relspd_rl = n >> 4
            v.relstep = n & 0xf
            return p + 2
        if idx in (5, 6):                              # $85 rest / $86 note off
            if idx == 6:
                v.volume = 0
            v.vibcnt = v.vibspd
            v.dur = v.defdur
            v.pat = p
            return None
        if idx == 7:                                   # $87 transpose
            v.transpose = s8(m.b(p))
            return p + 1
        if idx == 8:                                   # $88 vibrato
            v.vibdelay, v.vibdepth, v.vibspd = m.b(p), m.b(p + 1), m.b(p + 2)
            return p + 3
        if idx == 9:                                   # $89 pitch bend on note
            v.bend = (m.b(p + 1) << 8) | m.b(p)
            v.pstep = (m.b(p + 3) << 8) | m.b(p + 2)
            v.bendcnt = m.b(p + 4)
            v.flags |= 0x04
            return p + 5
        if idx == 10:                                  # $8a bend off
            v.flags &= ~0x04
            return p
        if idx in (11, 12):                            # $8b / $8c portamento
            v.flags |= 0x02 if idx == 11 else 0x20
            v.pstep = (m.b(p + 1) << 8) | m.b(p)
            v.pcnt = m.b(p + 2)
            return p + 3
        if idx == 13:                                  # $8d
            v.flags &= ~0x20
            return p
        if idx == 14:                                  # $8e legato on
            v.flags |= 0x10
            return p
        if idx == 15:                                  # $8f legato off
            v.flags &= ~0x10
            return p
        if idx == 16:                                  # $90 arpeggio off
            v.flags &= ~0x01
            return p
        if idx == 17:                                  # $91 volume down
            if v.setvol:
                v.setvol -= 1
            return p
        if idx == 18:                                  # $92 volume up
            if v.setvol < 0xf:
                v.setvol += 1
            return p
        if idx == 19:                                  # $93 start fade
            self.fadespd = self.fadecnt = 0x38
            return p
        if idx == 20:                                  # $94 SFX off
            return None
        raise RuntimeError('unknown command $%02x' % (0x80 + idx))
