#!/usr/bin/env python3
"""Vergleicht die DRONE im DSP-Emulator (build/drone/drone.mds) mit model/drone_dsp.py.
Aufruf: tools/compare_drone.py [NAME=WERT ...]   (ein Trig nach 0,05 s, 3 s lang)"""
import math, os, subprocess, sys, tempfile, wave
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "machines", "drone", "model"))
import drone_dsp as D  # noqa: E402
params = {n.upper(): int(v) for n, v in (a.split("=") for a in sys.argv[1:])}
SEC, PRE = 3.0, 0.05
with tempfile.TemporaryDirectory() as tmp:
    wav = os.path.join(tmp, "e.wav")
    cmd = [os.path.join(ROOT, "tools", "render", "mds-render"), os.path.join(ROOT, "build", "drone", "drone.mds"),
           wav, "-s", str(SEC), "-t", "0", "-f", str(PRE)]
    cmd += ["-t", "100"]
    for n, v in params.items():
        cmd += ["-p", f"{D.NAMES.index(n)}={v}"]
    info = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    w = wave.open(wav); d = w.readframes(w.getnframes())
emu = [int.from_bytes(d[i:i + 3], "little", signed=True) / 8388608 for i in range(0, len(d), 3)]
m = D.DroneDsp(params); mod = []
pre = int(PRE * 44100) // 32
for b in range(len(emu) // 32):
    if b == pre:
        m.trigger()
    mod += m.render_block()
def db(s, t):
    n = int(t * 44100); seg = s[n:n + 4410]
    return 20 * math.log10(math.sqrt(sum(x * x for x in seg) / len(seg)) + 1e-12)
n0, n1 = int(0.06 * 44100), int(0.5 * 44100)
corr = sum(a * b for a, b in zip(emu[n0:n1], mod[n0:n1])) / math.sqrt(
    sum(a * a for a in emu[n0:n1]) * sum(b * b for b in mod[n0:n1]) + 1e-30)
print(params or "Startpreset", "|", info.split(": ", 1)[1])
print("  RMS dB Emu|Modell: " + "  ".join(f"{t}s {db(emu, t):6.1f}|{db(mod, t):6.1f}" for t in (0.1, 0.5, 1.0, 2.0, 2.8)))
print(f"  Korrelation 0,06..0,5 s: {corr:.4f}")
