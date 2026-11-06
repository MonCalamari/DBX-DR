#!/usr/bin/env python3
"""DRONE – Modell in der Struktur des DSP-Codes (Gleitkomma, reines Python).

Prinzip der doergbox-DRONE (einstimmig), mit DSP-günstigen Bausteinen:
  - 3 naive Sägezähne (24-Bit-Phase = Sägezahn, kein PolyBLEP: "unpoliert"):
    OSC1 auf PTCH, OSC2 um DTUN verstimmt, OSC3 im Intervall INTV, halb gegenverstimmt;
    Sub = Rechteck eine Oktave tiefer (aus OSC1, gleiche Drift).
  - Drift: pro Oszillator wandernde Zufallsziele, pro Block nachgeführt.
  - Kubische Sättigung (1,5u − 0,5u³, u = x/1,5 auf ±1 begrenzt) → 4-Pol-Ladder (LP24),
    Resonanz = Rückkopplung von y4.
  - Feedback (Lyra-Prinzip) **vor** dem Filter: y4 läuft über eine Verzögerung (ganzzahlig,
    gleitet 1 Sample pro Block) zurück in die Sättigung → wird bei jedem Durchlauf vom
    Ladder abgedunkelt; die Resonanz facht das Eigenleben an.
  - Filter-Atmen: Parabel-Sinus pro Block, Tempo und Tiefe aus einem Regler (MOVE).
  - Kein Note-Off: TRIG blendet in 16 Blöcken (~12 ms) ein, danach hält der Klang.
Koeffizienten reihum in vier Gruppen, jede alle 4 Blöcke (wie im DSP). Halbe Skala wie im DSP: h = y/2.

  python3 drone_dsp.py [--set NAME=WERT ...] [-s SEK] [-o datei.wav]
"""
import argparse
import math
import os
import struct
import wave

FS = 44100.0
BLOCK = 32
NAMES = ["PTCH", "DTUN", "DRIV", "WARP", "CUT", "RES", "FDBK", "FTIM"]
DEFAULTS = {"PTCH": 17, "DTUN": 40, "DRIV": 50, "WARP": 40, "CUT": 45, "RES": 50, "FDBK": 70, "FTIM": 64}
W_RATE = 0.0006              # WARP: Wanderung folgt dem Ziel (alle 4 Blöcke) – Zeitkonstante ~5 s
W_MASK = 0xFFE000             #   neues Ziel mit Wahrscheinlichkeit 1/2048 je 4 Blöcke (~6 s)
W_OCT = 0.25                 #   OSC2/OSC3 bis ±3 Halbtöne (mal WARP^2)
W_DLY = 0.35                 #   Feedback-Zeit bis ±0,35 Oktaven (mal WARP^2)
IV3 = -1.0                   # OSC3 fest eine Oktave tiefer (0.3; bis 0.2 Regler INTV)
K_FBX = 0.6                  # FDBK blendet die Oszillatoren bis auf 40 % zurück
FB_MAX = 1.5                 # Feedback-Verstärkung höchstens (> 1: Schleife schwingt sich auf), phasengedreht
D_MIN = 44                   # FTIM 127 = 1 ms
O3 = 0.9                     # Pegel OSC3 (0.1: 0,5)
SUBL = 0.6                   # Sub fest (0.1: Regler SUB, 0..0,8)
LIM_THR, LIM_ATK, LIM_REL = 0.9, 0.8, 1 / 64       # Begrenzer, geregelt alle 4 Blöcke (0.4: sanfter)

RING = 1536                  # Spurspeicher (Wörter)
D_MAX = 1500                 # FTIM 0 = 34 ms
G_MAX = 0.75                 # Ladder-Koeffizient höchstens (Stabilität, ~5 kHz)
LOG_C0 = math.log2(2 * math.pi * 30 / FS)       # CUT 0 = 30 Hz
LOG_CC = math.log2(8000 / 30)                   # CUT 127 ≈ 8 kHz (nominell)
K_OUT = 0.15                 # Ausgangspegel (mal 4 im DSP); 0.4: −6 dB Reserve für MD-Filter mit hoher Q


def pitch_value(v):
    """MDS-Transform pitch: Oszillator-Inkrement P, f = P·44100/2^21."""
    return round(1625.0 * 2 ** (math.log2(538240.0 / 1625.0) * v / 127.0))


class DroneDsp:
    def __init__(self, params=None):
        p = dict(DEFAULTS)
        p.update(params or {})
        self.p = p
        self.act = False
        self.env = 0.0
        self.ph = [0.0, 0.33 * 2 - 1, 0.67 * 2 - 1, 0.0]   # Phasen als Sägezahn −1..1
        self.h = [0.0] * 4                                 # Ladder (halbe Skala)
        self.dr = [0.0] * 3
        self.w = [0.0] * 2                                 # WARP-Wanderung OSC2, OSC3
        self.wt = [0.0] * 2
        self.drt = [0.0] * 3
        self.rnd = 0x5A5A5B
        self.ring = [0.0] * RING
        self.wp = 0
        self.written = 0
        self.dly = self.dtgt = 1500
        self.rot = 0
        self.inc = [0.0] * 4
        self.g = self.r = self.fb = self.gres = 0.0
        self.dg = 1.0
        self.lim = 1.0
        self.pkm = 0.0

    # 24-Bit-Zufall wie im DSP: s = s·1664525 mod 2^24, Wert vorzeichenbehaftet
    def rand(self):
        self.rnd = (self.rnd * 1664525) & 0xFFFFFF
        return self.rnd

    def trigger(self):
        self.act = True

    def render_block(self):
        if not self.act:
            return [0.0] * BLOCK
        p = self.p
        P = pitch_value(p["PTCH"])
        t = p["DTUN"] / 128.0                              # unipolar, linear
        det, dc = t / 12.0, (4 + 60 * t) / 1200.0          # Oktaven: bis 1 Halbton, Drift 4..64 Cent
        iv = IV3
        dsq = (p["DRIV"] / 128.0) ** 2                     # unipolar_square
        wsq = (p["WARP"] / 128.0) ** 2                     # unipolar_square
        cut, res = p["CUT"] / 128.0, p["RES"] / 128.0
        tf, tt = p["FDBK"] / 128.0, p["FTIM"] / 128.0

        # reihum eine Gruppe pro Block (wie im DSP)
        rot, self.rot = self.rot, (self.rot + 1) & 3
        if rot == 0:                                        # Drift (selten neues Ziel, langsam nachführen)
            for k in range(3):
                if (self.rand() & 0xF80000) == 0:
                    t = self.rand()
                    self.drt[k] = (t - (1 << 24) if t & 0x800000 else t) / 8388608.0
                self.dr[k] += (self.drt[k] - self.dr[k]) * 0.016
            for k in range(2):                              # WARP: langsame, unregelmäßige Wanderung
                if (self.rand() & W_MASK) == 0:
                    t = self.rand()
                    self.wt[k] = (t - (1 << 24) if t & 0x800000 else t) / 8388608.0
                self.w[k] += (self.wt[k] - self.w[k]) * W_RATE
            if self.pkm >= LIM_THR:                         # Begrenzer, Spitze der letzten 4 Blöcke
                self.lim *= LIM_ATK
            else:
                self.lim += (1 - self.lim) * LIM_REL
            self.pkm = 0.0
            self.dly += max(-4, min(4, self.dtgt - self.dly))   # Verzögerung gleitet
        elif rot == 1:                                      # Tonhöhen
            f0 = P * 8 / 2 ** 24                            # Zyklen pro Sample (Phase 0..1)
            inc = [f0 * 2 ** (self.dr[0] * dc),
                   f0 * 2 ** (det + self.dr[1] * dc + W_OCT * wsq * self.w[0]),
                   f0 * 2 ** (iv - det / 2 + self.dr[2] * dc + W_OCT * wsq * self.w[1])]
            inc = [min(i, 0.5) for i in inc]
            self.inc = inc + [inc[0] / 2]
        elif rot == 2:                                      # CUT
            self.g = min(2 ** (LOG_C0 + LOG_CC * cut), G_MAX)
        else:                                               # Resonanz, Pegel, DRIV, Feedback
            self.r = 0.97 * 4 * res
            self.gres = K_OUT * (1 + self.r / 4) * (1 - 0.4 * dsq)
            fbc = 1 - (1 - tf) ** 2
            self.dg = (1 + 7 * dsq) * (1 - K_FBX * fbc)    # Drive 1..8, von FDBK zurückgeblendet
            self.fb = -FB_MAX * fbc
            e = -math.log2(D_MAX / D_MIN) * tt + W_DLY * wsq * self.w[0]
            self.dtgt = int(D_MAX * 2 ** min(e, 0.0))
        inc, g, r, fb, dg = self.inc, self.g, self.r, self.fb, self.dg
        gout = self.gres * self.env * self.lim * 4
        self.env = min(1.0, self.env + 1 / 16)
        D = self.dly

        # Feedback lesen (nur bereits beschriebener Speicher)
        if D <= self.written:
            d = [self.ring[(self.wp - D + i) % RING] for i in range(BLOCK)]
        else:
            d = [0.0] * BLOCK

        out = []
        h = self.h
        for i in range(BLOCK):
            ph = self.ph
            for k in range(4):
                ph[k] += inc[k] * 2
                if ph[k] >= 1:
                    ph[k] -= 2
            sq = 1.0 if ph[3] >= 0 else -1.0
            xs = dg * (0.75 * (ph[0] + 0.8 * ph[1] + O3 * ph[2]) + 0.75 * SUBL * sq) - r * 2 * h[3] + fb * 2 * d[i]
            u = max(-1.0, min(1.0, xs / 1.5))
            inp = (1.5 * u - 0.5 * u ** 3) / 2              # halbe Skala
            h[0] += g * (inp - h[0])
            h[1] += g * (h[0] - h[1])
            h[2] += g * (h[1] - h[2])
            h[3] += g * (h[2] - h[3])
            self.ring[(self.wp + i) % RING] = inp            # Feedback-Abgriff vor dem Filter
            out.append(max(-1.0, min(1.0, h[3] * gout)))
            self.raw_peak = max(getattr(self, "raw_peak", 0.0), abs(h[3] * gout))
        self.pkm = max(self.pkm, max(abs(x) for x in out[::2]))   # jedes zweite Sample wie im DSP
        self.wp = (self.wp + BLOCK) % RING
        self.written = min(RING, self.written + BLOCK)
        return out


def write_wav(path, samples):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(int(FS))
        w.writeframes(b"".join(struct.pack("<i", int(round(max(-1.0, min(1.0, s)) * 8388607)))[:3]
                               for s in samples))
    return max((abs(s) for s in samples), default=0.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("-s", type=float, default=6.0)
    ap.add_argument("-o", default="demos/model.wav")
    a = ap.parse_args()
    params = {k.upper(): int(v) for k, v in (x.split("=") for x in a.set)}
    m = DroneDsp(params)
    m.trigger()
    out = []
    for _ in range(int(a.s * FS / BLOCK)):
        out += m.render_block()
    peak = write_wav(a.o, out)
    print(f"{a.o}: Spitze {20 * math.log10(peak + 1e-12):.1f} dBFS")


if __name__ == "__main__":
    main()
