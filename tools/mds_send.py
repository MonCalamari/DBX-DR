#!/usr/bin/env python3
"""Überträgt USR-Machines zur Machinedrum (MDX X.20) – nach MDX_MachineDumpProtocol.txt.

  tools/mds_send.py info                          Fähigkeiten + Liste der USR-Plätze (nur lesen)
  tools/mds_send.py send DATEI.mds|.syx [--slot N]   Machine übertragen (geschlossene Schleife:
                                                  jedes Paket wird von der MD bestätigt)
  Optionen: --port /dev/snd/midiC1D0 (Standard: erstes Gerät mit "TM-1" bzw. das einzige),
            --timeout SEK (Antwortzeit je Nachricht, Standard 2), -v (alle Nachrichten zeigen)

Ablauf beim Senden (Protokoll "Receiving a machine"): Dump Header, Data Packets, End of Dump –
nach jeder Nachricht auf die Antwort warten. ACK = weiter, WAIT = weiter warten, NAK CORRUPT mit
Detail = genanntes Paket wiederholen, jede andere NAK/CANCEL = Abbruch. Ohne Antwort wird die
aktuelle Nachricht unverändert wiederholt (laut Protokoll unschädlich), nach 3 Versuchen Abbruch.
Das letzte ACK (Status 1) bestätigt Prüfung und dauerhafte Speicherung; ein abgebrochener
Versuch ersetzt laut Protokoll nie die schon installierte Machine.

Die Slot-Nummer ist nur ein Vorschlag: das Gerät kann die Machine woanders ablegen.
Greift direkt auf das ALSA-Rawmidi-Gerät zu (kein Paket nötig).
"""
import argparse
import glob
import os
import select
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vendor", "mds-tools", "tools"))
from mds_to_syx import convert  # noqa: E402  (jmammas Erzeuger, prüft das Paket gründlich)

SYSEX_INIT = bytes((0xF0, 0x00, 0x20, 0x3C, 0x02, 0x00))
CMD = {0x01: "Dump Header", 0x02: "Data Packet", 0x03: "Dump Request", 0x04: "Query Machine Slots",
       0x05: "Machine Slots Reply", 0x06: "Capability Request", 0x07: "Capability Reply",
       0x7B: "End of Dump", 0x7C: "WAIT", 0x7D: "CANCEL", 0x7E: "NAK", 0x7F: "ACK"}
NAK_REASON = {1: "Malformed message", 2: "Unsupported version", 3: "Unsupported command",
              4: "Invalid slot", 5: "Too large", 6: "Corrupt data", 7: "Incompatible machine",
              8: "Reserved", 9: "Receiver not ready", 10: "Slot in use", 11: "Busy",
              12: "Storage failure", 13: "DSP activation failure", 14: "Transaction conflict",
              15: "Internal fault", 16: "DSP program memory full", 17: "Library full",
              18: "Cycle budget"}
NAK_DE = {5: "Paket zu groß", 7: "Machine passt nicht zu dieser Firmware",
          9: "Gerät nicht bereit", 10: "Platz belegt (Machine auf einer Spur in Benutzung?)",
          11: "Gerät beschäftigt", 12: "Speicherfehler (Flash)", 16: "DSP-Programmspeicher voll",
          17: "Bibliothek voll", 18: "Rechenzeit (Paket nicht zertifiziert?)"}


# --------------------------------------------------------------------------- Nachrichten
def pack7(body):
    out = bytearray()
    for at in range(0, len(body), 7):
        group = body[at:at + 7]
        out.append(sum((v >> 7) << (6 - i) for i, v in enumerate(group)))
        out.extend(v & 0x7F for v in group)
    return bytes(out)


def unpack7(data):
    out = bytearray()
    at = 0
    while at < len(data):
        mask, group = data[at], data[at + 1:at + 8]
        out.extend(v | (((mask >> (6 - i)) & 1) << 7) for i, v in enumerate(group))
        at += 1 + len(group)
    return bytes(out)


def build(command, trans, slot, seq, body=b""):
    """slot: Draht-Slot 0..n-1 oder 0x7F; seq: Paketnummer oder 0x7F."""
    checked = bytes((trans >> 7 & 0x7F, trans & 0x7F, slot, seq, 0)) + pack7(body)
    cs, ln = sum(checked) & 0x3FFF, len(checked) + 4
    return SYSEX_INIT + bytes((0x74, 1, command)) + checked + bytes((cs >> 7, cs & 0x7F, ln >> 7, ln & 0x7F, 0xF7))


def parse(msg):
    """Gibt dict zurück oder None, wenn es keine (gültige) USR-Machine-Nachricht ist."""
    if len(msg) < 19 or not msg.startswith(SYSEX_INIT + b"\x74\x01") or msg[-1] != 0xF7:
        return None
    command, checked, tail = msg[8], msg[9:-5], msg[-5:-1]
    cs, ln = tail[0] << 7 | tail[1], tail[2] << 7 | tail[3]
    if cs != sum(checked) & 0x3FFF or ln != len(checked) + 4:
        return {"command": command, "error": "Prüfsumme/Länge falsch"}
    return {"command": command, "trans": checked[0] << 7 | checked[1], "slot": checked[2],
            "seq": checked[3], "body": unpack7(checked[5:])}


def split_syx(data):
    msgs, at = [], 0
    while True:
        a = data.find(b"\xF0", at)
        if a < 0:
            return msgs
        b = data.find(b"\xF7", a)
        if b < 0:
            raise ValueError("unvollständige SysEx-Nachricht in der Datei")
        msgs.append(data[a:b + 1])
        at = b + 1


def retarget(msg, slot):
    """Draht-Slot einer Nachricht ändern und Prüfsumme neu rechnen."""
    m = bytearray(msg)
    m[11] = slot
    checked = m[9:-5]
    cs = sum(checked) & 0x3FFF
    m[-5], m[-4] = cs >> 7, cs & 0x7F
    return bytes(m)


# --------------------------------------------------------------------------- MIDI-Gerät
def find_port():
    ports = sorted(glob.glob("/dev/snd/midiC*D*"))
    if not ports:
        raise SystemExit("Kein MIDI-Gerät gefunden (/dev/snd/midiC*D*). Interface eingesteckt?")
    try:
        listing = subprocess.run(["amidi", "-l"], capture_output=True, text=True).stdout
        for line in listing.splitlines():
            if "TM-1" in line:
                card, dev = line.split()[1][3:].split(",")[:2]
                return f"/dev/snd/midiC{card}D{dev}"
    except OSError:
        pass
    if len(ports) > 1:
        raise SystemExit("Mehrere MIDI-Geräte: bitte --port angeben: " + ", ".join(ports))
    return ports[0]


class Midi:
    def __init__(self, path, verbose=False):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.buf, self.verbose, self.path = bytearray(), verbose, path
        self.drain()

    def drain(self):
        while select.select([self.fd], [], [], 0.05)[0]:
            try:
                if not os.read(self.fd, 4096):
                    break
            except BlockingIOError:
                break
        self.buf.clear()

    def send(self, msg):
        if self.verbose:
            p = parse(msg)
            print(f"  → {CMD.get(p['command'], hex(p['command']))} seq={p['seq']} ({len(msg)} Bytes)")
        view = memoryview(msg)
        while view:
            select.select([], [self.fd], [], 1.0)
            try:
                n = os.write(self.fd, view)
            except BlockingIOError:
                continue
            view = view[n:]

    def receive(self, timeout):
        """Nächste USR-Machine-Nachricht (geparst) oder None nach Ablauf der Zeit."""
        end = time.monotonic() + timeout
        while True:
            a = self.buf.find(b"\xF0")
            if a >= 0:
                b = self.buf.find(b"\xF7", a)
                if b >= 0:
                    raw = bytes(self.buf[a:b + 1])
                    del self.buf[:b + 1]
                    p = parse(raw)
                    if p is not None:
                        if self.verbose:
                            print(f"  ← {CMD.get(p['command'], hex(p['command']))} seq={p.get('seq')}"
                                  + (f" body={p['body'].hex()}" if p.get("body") and len(p["body"]) <= 16 else ""))
                        return p
                    continue
            elif self.buf:
                self.buf.clear()
            left = end - time.monotonic()
            if left <= 0:
                return None
            if select.select([self.fd], [], [], left)[0]:
                try:
                    data = os.read(self.fd, 4096)
                except BlockingIOError:
                    continue
                # MIDI-Echtzeitbytes (Clock usw.) gehören nicht zur Nachricht
                self.buf.extend(x for x in data if x < 0xF8)


# --------------------------------------------------------------------------- Befehle
def describe_nak(body):
    reason = body[1] if len(body) > 1 else 0
    detail = body[2] << 8 | body[3] if len(body) > 3 else 0
    txt = NAK_REASON.get(reason, f"Grund {reason}")
    if reason in NAK_DE:
        txt += f" – {NAK_DE[reason]}"
    return reason, detail, txt


def request(midi, command, timeout, want):
    trans = int(time.time()) & 0x3FFF
    midi.send(build(command, trans, 0x7F, 0x7F))
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        p = midi.receive(end - time.monotonic())
        if p is None:
            break
        if p["command"] == want:
            return p["body"]
        if p["command"] == 0x7E:
            raise SystemExit(f"Abgelehnt: {describe_nak(p['body'])[2]}")
    raise SystemExit(f"Keine Antwort auf {CMD[command]} – Machinedrum an, MIDI-Kabel in beide Richtungen, "
                     "X.20-Firmware?")


def cmd_info(midi, a):
    cap = request(midi, 0x06, a.timeout, 0x07)
    (max_pkg, max_words, max_sysex, total_words, free_words, lib_cap, lib_free,
     slots, first_id, last_id, req1, codec, _r, features, _r2, dsp_limit) = struct.unpack(">7H4BHHIII", cap[:34])
    feats = [n for b, n in enumerate(["Samples", "RAM", "Kategorie-Reihenfolge", "Trig-Info", "Tempo",
                                      "MKI-Zyklen"]) if features >> b & 1]
    print(f"Fähigkeiten: {slots} Plätze, DSP-Programmspeicher frei {free_words}/{total_words} Wörter, "
          f"Bibliothek frei {lib_free}/{lib_cap} Bytes, DSP-Grenze {dsp_limit} Takte/Block, "
          f"max. Paket {max_pkg} Bytes / {max_words} Wörter")
    print("Funktionen:", ", ".join(feats) or "–")
    rep = request(midi, 0x04, a.timeout, 0x05)
    gen = struct.unpack(">I", rep[:4])[0]
    print(f"USR-Plätze (Generation {gen}):")
    for i in range(16):
        r = rep[4 + 22 * i:4 + 22 * (i + 1)]
        if len(r) < 22:
            break
        flags, model, words, size = r[0], r[1], r[2] << 8 | r[3], r[4] << 8 | r[5]
        alias = r[6:14].rstrip(b"\0").decode("ascii", "replace")
        if not flags & 0x01:
            print(f"  U{i + 1:02d}  leer")
            continue
        st = [n for bit, n in ((0x04, "gültig"), (0x08, "in Benutzung"), (0x10, "beim Start aus")) if flags & bit]
        print(f"  U{i + 1:02d}  {alias:8s}  {words:4d} Wörter  {size:5d} Bytes  ID {r[14:22].hex()}  "
              f"[{', '.join(st)}]")


def cmd_send(midi, a):
    data = open(a.file, "rb").read()
    if data[:4] == b"MDS1":
        msgs = split_syx(convert(data, a.slot or 1))
        content_id = data[88:96]
    else:
        msgs = split_syx(data)
        content_id = None
        if a.slot:
            msgs = [retarget(m, a.slot - 1) for m in msgs]
    ps = [parse(m) for m in msgs]
    if (not ps or any(p is None or "error" in p for p in ps) or ps[0]["command"] != 0x01
            or ps[-1]["command"] != 0x7B):
        raise SystemExit("Datei ist keine gültige USR-Machine-Übertragung (Header … End of Dump)")
    if content_id is None:
        content_id = ps[0]["body"][10:18]
    wire_slot = ps[0]["slot"]
    count = ps[0]["body"][4]
    print(f"Sende {os.path.basename(a.file)}: {len(msgs)} Nachrichten, {count} Datenpakete, "
          f"Ziel-Vorschlag U{wire_slot + 1:02d}, über {midi.path}")

    def index_of(command, seq):
        for i, p in enumerate(ps):
            if p["command"] == command and (command == 0x01 or p["seq"] == seq):
                return i
        return None

    i, tries, t0 = 0, 0, time.monotonic()
    while i < len(msgs):
        midi.send(msgs[i])
        name = CMD[ps[i]["command"]] + (f" {ps[i]['seq'] + 1}/{count}" if ps[i]["command"] == 0x02 else "")
        deadline = time.monotonic() + a.timeout
        while True:
            p = midi.receive(max(0.0, deadline - time.monotonic()))
            if p is None:
                tries += 1
                if tries > 3:
                    raise SystemExit(f"Keine Antwort auf {name} (3× wiederholt) – abgebrochen. "
                                     "Die bisher installierte Machine bleibt unverändert.")
                print(f"  keine Antwort auf {name}, wiederhole ({tries}/3)")
                break
            if p.get("trans") != ps[0]["trans"]:
                continue                              # fremde Nachricht
            c = p["command"]
            if c == 0x7C:                             # WAIT: Fenster neu starten
                deadline = time.monotonic() + a.timeout
                continue
            if c == 0x7F:
                tries = 0
                body = p["body"]
                if ps[i]["command"] == 0x7B:
                    status = body[1] if len(body) > 1 else 0
                    if status != 1:
                        raise SystemExit("End of Dump bestätigt, aber nicht als installiert (Status "
                                         f"{status}) – bitte am Gerät prüfen.")
                    gen = struct.unpack(">I", body[2:6])[0]
                    ok = body[6:14] == content_id
                    print(f"Installiert ({time.monotonic() - t0:.1f} s), Bibliotheks-Generation {gen}, "
                          f"Kennung {'stimmt' if ok else 'WEICHT AB: ' + body[6:14].hex()}.")
                    return
                print(f"  {name}: ok")
                i += 1
                break
            if c == 0x7E:
                reason, detail, txt = describe_nak(p["body"])
                if reason == 6 and detail:            # Reparatur: genannte Nachricht wiederholen
                    j = index_of(detail >> 8, detail & 0xFF)
                    if j is not None and tries < 3:
                        tries += 1
                        print(f"  Reparatur angefordert: {CMD.get(detail >> 8, hex(detail >> 8))} "
                              f"seq {detail & 0xFF}, wiederhole")
                        i = j
                        break
                raise SystemExit(f"Abgelehnt bei {name}: {txt}. Die bisher installierte Machine bleibt unverändert.")
            if c == 0x7D:
                raise SystemExit(f"Machinedrum hat abgebrochen bei {name}: {describe_nak(p['body'])[2]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("befehl", choices=["info", "send"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--slot", type=int, help="Ziel-Vorschlag 1..16 (Standard: aus der Datei bzw. 1)")
    ap.add_argument("--port", help="Rawmidi-Gerät, z. B. /dev/snd/midiC1D0")
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("-v", action="store_true", help="alle Nachrichten anzeigen")
    a = ap.parse_args()
    if a.befehl == "send" and not a.file:
        ap.error("send braucht eine .mds- oder .syx-Datei")
    if a.slot is not None and not 1 <= a.slot <= 16:
        ap.error("--slot muss 1..16 sein")
    midi = Midi(a.port or find_port(), a.v)
    try:
        (cmd_info if a.befehl == "info" else cmd_send)(midi, a)
    except ValueError as e:
        raise SystemExit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
