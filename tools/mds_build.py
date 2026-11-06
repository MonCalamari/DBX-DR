#!/usr/bin/env python3
"""Baut eine Machine: asm56300 (Wine) -> .cld -> mds_pack.py -> .mds [-> .syx].

  tools/mds_build.py machines/kick2                 assemblieren + packen (unzertifiziert)
  tools/mds_build.py machines/kick2 --certify       zusätzlich Zyklen messen und stempeln
  tools/mds_build.py machines/kick2 --certify       zusätzlich <name>.syx (Ziel-Slot im SysEx = 1;
                                                    das Gerät legt sie dort ab, wo man im Machine Manager will)
  ... --slot N                                      anderer Ziel-Slot im SysEx (selten nötig)
  tools/mds_build.py --verify                       Toolchain gegen jmammas Beispiele prüfen

Erwartet im Ordner <name>.asm56k und <name>.json, optional <name>.pbm (Logo 32x24);
Ausgabe nach build/<name>/.
Pfade zu den Werkzeugen über Umgebungsvariablen anpassbar:
  MDS_WINEPREFIX  (Standard ~/dsp-tools/wine-symphony)
  MDS_DSP_TOOLS   (Standard <prefix>/drive_c/Symphony-Studio/dsp56720-devtools/dist/gcc/bin)
"""
import argparse
import glob
import os
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "vendor", "mds-tools")
PACK = os.path.join(TOOLS, "tools", "mds_pack.py")
TO_SYX = os.path.join(TOOLS, "tools", "mds_to_syx.py")
CERTIFIER = os.path.join(TOOLS, "build", "mds-certifier")
EXAMPLES = os.path.join(ROOT, "vendor", "MDS", "machines", "examples")

PREFIX = os.environ.get("MDS_WINEPREFIX", os.path.expanduser("~/dsp-tools/wine-symphony"))
DSP_TOOLS = os.environ.get("MDS_DSP_TOOLS", os.path.join(
    PREFIX, "drive_c", "Symphony-Studio", "dsp56720-devtools", "dist", "gcc", "bin"))
ENV = dict(os.environ, WINEPREFIX=PREFIX, WINEDEBUG="-all")


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, env=ENV, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f"Fehlgeschlagen: {' '.join(os.path.basename(c) for c in cmd[:3])} (Exit {r.returncode})")
    return r


def assemble(src, out_stem):
    """asm56300 im absoluten Modus; Include-Pfad = Ordner der Quelle."""
    os.makedirs(os.path.dirname(out_stem), exist_ok=True)
    cld, lst = out_stem + ".cld", out_stem + ".lst"
    for f in (cld, lst):
        if os.path.exists(f):
            os.remove(f)
    r = run(["wine", os.path.join(DSP_TOOLS, "asm56300.exe"), "-q", "-a",
             "-b" + cld, "-l" + lst, "-i" + os.path.dirname(os.path.abspath(src)), os.path.abspath(src)],
            cwd=os.path.dirname(os.path.abspath(src)))
    text = (r.stdout + r.stderr).strip()
    if not os.path.exists(cld):
        sys.stderr.write(text + "\n")
        raise SystemExit("asm56300 hat keine .cld erzeugt")
    return cld, text


def pack(definition, cld, mds, certify=False, report=None, max_cycles=None, bitmap=None):
    cmd = [sys.executable, PACK, definition, cld, "--format", "cld", "--dsp-tools", DSP_TOOLS, "-o", mds]
    if bitmap:
        cmd += ["--bitmap", bitmap]
    if certify:
        cmd += ["--certify", "--certifier", CERTIFIER]
        if report:
            cmd += ["--report", report]
    elif max_cycles:
        cmd += ["--max-track-cycles", str(max_cycles)]
    return run(cmd)


def sections(pkg):
    h = lambda o, f: struct.unpack_from(f, pkg, o)[0]
    po, pl = h(32, ">I"), h(36, ">I")
    ro, io, n = h(44, ">I"), h(48, ">I"), h(24, ">H")
    ds, do = h(52, ">H"), h(56, ">I")
    return {"Programm": pkg[po:po + pl], "Relokationen": pkg[ro:io], "Importe": pkg[io:io + 4 * n],
            "Beschreibung": pkg[do:do + ds], "Einsprünge": pkg[64:70], "Ressourcen": pkg[22:30]}


def verify():
    """Assembliert jmammas Beispiele und vergleicht byteweise mit den mitgelieferten .mds."""
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        for src in sorted(glob.glob(os.path.join(EXAMPLES, "**", "*.asm56k"), recursive=True)):
            stem = os.path.splitext(src)[0]
            name = os.path.relpath(stem, EXAMPLES)
            if not (os.path.exists(stem + ".json") and os.path.exists(stem + ".mds")):
                continue
            out = os.path.join(tmp, os.path.basename(stem))
            cld, _ = assemble(src, out)
            pack(stem + ".json", cld, out + ".mds", max_cycles=1)
            ours, ref = sections(open(out + ".mds", "rb").read()), sections(open(stem + ".mds", "rb").read())
            bad = [k for k in ref if ours[k] != ref[k]]
            ok &= not bad
            print(f"{'ANDERS' if bad else 'GLEICH'}  {name} ({len(ref['Programm']) // 3} Wörter)"
                  + (f": {', '.join(bad)}" if bad else ""))
    print("Ergebnis:", "alles byte-identisch" if ok else "ABWEICHUNGEN")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("machine", nargs="?", help="Machine-Ordner, z. B. machines/kick2")
    ap.add_argument("--certify", action="store_true")
    ap.add_argument("--slot", type=int, default=1, help="Ziel-Slot im SysEx (nur Vorschlag, Standard 1)")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify:
        sys.exit(0 if verify() else 1)
    if not a.machine:
        ap.error("Machine-Ordner fehlt")
    name = os.path.basename(os.path.normpath(a.machine))
    src = os.path.join(a.machine, name + ".asm56k")
    definition = os.path.join(a.machine, name + ".json")
    out = os.path.join(ROOT, "build", name, name)
    cld, msg = assemble(src, out)
    if msg:
        print(msg)
    bitmap = os.path.join(a.machine, name + ".pbm")      # Logo (32x24, PBM P1), falls vorhanden
    r = pack(definition, cld, out + ".mds", certify=a.certify, report=out + ".report.json" if a.certify else None,
             max_cycles=None if a.certify else 1, bitmap=bitmap if os.path.exists(bitmap) else None)
    print((r.stdout + r.stderr).strip())
    if a.certify:                                       # .syx nur aus zertifiziertem Paket
        r = run([sys.executable, TO_SYX, out + ".mds", "--slot", str(a.slot), "-o", out + ".syx"])
        print((r.stdout + r.stderr).strip())


if __name__ == "__main__":
    main()
