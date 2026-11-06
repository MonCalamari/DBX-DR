# DBX-DR – Dark Drone for the Elektron Machinedrum

A USR machine for the Machinedrum running jmamma's MDX firmware **X.20**
(Machine Dump Standard, MDS 1.1).

DBX-DR is a single-voice drone, ported in principle from the DRONE of my DIY groovebox
(doergbox, Teensy 4.1, built on the [MicroDexed-touch](https://codeberg.org/positionhigh/MicroDexed-touch)
hardware, with my own firmware written from scratch). It is made as a dark ground tone for EBM,
techno and dark minimal: low, grumbling, raw, not polished.

Three detuned, deliberately unsmoothed saw oscillators and a square sub go through a saturation
stage into a 4-pole ladder lowpass with resonance. The heart of the machine is a **feedback loop
in front of the filter**: the saturated signal runs through a short delay line and back into the
saturation, phase-inverted, with a gain of up to 1.5. Above 1 the loop starts to oscillate on its
own and fights with the drone – from slow growling to metallic whistling. **WARP** lets two of the
oscillators and the loop's delay drift slowly and irregularly away from the root and back, so the
sound keeps changing without any periodic wobble.

Runs on **MKI and MKII, on all 16 tracks**. Certified cycle counts per track and block:
MKII 2,610 · MKI (DRAM) 3,017 · MKI (SRAM) 2,681. Tested on a MKII +Drive UW.

## Playing it

- **Trigger once, then play with the knobs.** A trigger fades the drone in (~12 ms); after that it
  holds forever – the Machinedrum has no note-off. Further triggers or notes only change the pitch,
  without restarting or clicking (continuous retrigger). Stop it by muting the track or with the
  track level.
- **Tonal:** PTCH follows the keyboard, MIDI notes and the tuning.
- The output peaks around −9 dBFS on purpose, leaving headroom for the Machinedrum's own track
  filter with high resonance. Raise the track level if needed.
- Great together with the Machinedrum's track filter: turn **FLTW** (width) down and **FLTF**
  (base) up so the two filter edges run against each other, with high **FLTQ** – this gets you
  anything from strange high tones to the deepest rumble.

## Knobs

| Knob | Function |
|---|---|
| **PTCH** | Pitch of the root oscillator (34 Hz–11 kHz, follows notes). Sub and OSC3 sit one octave below |
| **DTUN** | Detune of OSC2 (0–100 cent) **and** random pitch drift of all oscillators (4–64 cent) |
| **DRIV** | Drive of the oscillators into the saturation, 1–8×, from warm to scratchy |
| **WARP** | Slow, irregular wandering of OSC2 and OSC3 (up to ±3 semitones, new targets every few seconds, moving over seconds) and of the feedback delay. The root oscillator stays put, so you stay in key. 0 = no movement |
| **CUT** | Ladder lowpass, 30 Hz to ~5 kHz |
| **RES** | Resonance, up to just before self-oscillation |
| **FDBK** | Feedback amount 0–1.5 (phase-inverted). Also pulls the oscillators back to 40 %, so the loop can take over |
| **FTIM** | Feedback delay 34 ms → 1 ms: sets the pitch of the loop's own tones (≈15 Hz growl to ≈500 Hz whistle) |

Default sound: PTCH 17 (≈ 74 Hz, about D), DTUN 40, DRIV 50, WARP 40, CUT 45, RES 50, FDBK 70, FTIM 64.

## Status & known limitations

This is the first public version, a fresh take on an ambient/drone machine. Known weak spots:

- At extreme settings (high RES and FDBK together) you can still hear some crackling. A simple
  block-wise limiter catches most of it; I find the rest acceptable for this kind of sound.
- Comments in the source and tools are in **German**.
- Tested on hardware so far on a single MKII +Drive UW; on MKI it is certified, not yet played.

## How this was made

The sound idea comes from the DRONE of my doergbox groovebox, which has grown over more than 300
iterations, developed together with AI. For this Machinedrum port I set the goals, made the sound
decisions and tested on the hardware; [Claude Code](https://claude.com/claude-code) (Anthropic)
wrote the DSP56303 code, the Python model and the tooling, and ran the certification. The port
follows the principle, not the exact original: the feedback loop sits in front of the filter here,
and WARP is new.

## Installation

Download the files from the [Releases](../../releases) page:

- `drone-<version>.syx` – ready to send.
- `drone-<version>.mds` – the certified package.

On the Machinedrum, open the **Machine Manager**, select the USR slot you want and press
**RECEIVE**. The slot number stored in the file is only a suggestion; the device places the
machine where you choose.

The transfer works best when every message is acknowledged before the next one is sent – the
Machinedrum answers with WAIT while it writes to flash. `tools/mds_send.py` does exactly that
(Linux, ALSA raw MIDI, no extra packages):

```sh
python3 tools/mds_send.py send drone-0.5.mds      # or the .syx
```

With a plain SysEx tool, leave a pause between the messages, e.g. `amidi -s drone-0.5.syx -i 300`.
Sending everything in one go made my +Drive report "Flash Congestion" once.

A failed transfer never replaces the machine already installed in a slot.

As jmamma points out: only install MDS machines from developers you trust – machine code runs
directly on the DSP.

## Repository layout

| Path | Content |
|---|---|
| `machines/drone/drone.asm56k` | DSP56303 source |
| `machines/drone/drone.json` | MDS machine definition (parameters, memory, relocations, imports) |
| `machines/drone/drone.pbm` | Machine logo, 32 × 24 pixels |
| `machines/drone/model/drone_dsp.py` | Python model with the same structure as the DSP code |
| `tools/mds_build.py` | Assemble, package, certify, create SysEx |
| `tools/mds_send.py` | Send a machine to the Machinedrum with acknowledgement (Linux) |
| `tools/render/` | `mds-render`: runs a `.mds` in the certifier's DSP emulator and writes a WAV |
| `tools/compare_drone.py` | Compares the emulated DSP code with the Python model |
| `vendor/MDS`, `vendor/mds-tools` | jmamma's specification and tools (git submodules) |

## Building

Tested on Fedora Linux. You need Python 3.10+, CMake, a C++17 compiler, `patch` and Wine.

1. Clone with submodules:
   `git clone --recurse-submodules https://github.com/MonCalamari/DBX-DR.git`
2. Assembler: install NXP **Symphony Studio** (Windows installer, contains `asm56300.exe`
   and `cldlod.exe`) under Wine. It is proprietary and not part of this repository.
   `tools/mds_build.py` looks for it in the Wine prefix `~/dsp-tools/wine-symphony`;
   override with `MDS_WINEPREFIX` or `MDS_DSP_TOOLS`.
   `python3 tools/mds_build.py --verify` checks the toolchain against jmamma's example
   machines (all must be byte-identical).
3. Certifier (on Linux, the emulator build needs two extra include paths):
   ```sh
   cmake -S vendor/mds-tools -B vendor/mds-tools/build -DCMAKE_BUILD_TYPE=Release \
         -DCMAKE_CXX_FLAGS="-I$PWD/vendor/mds-tools/external/dsp56300/source"
   cmake --build vendor/mds-tools/build -j"$(nproc)"   # stops at vtuneSdk the first time
   ln -sfn "$PWD/vendor/mds-tools/external/dsp56300/source/vtuneSdk" \
           vendor/mds-tools/build/certifier/dsp56300/source/vtuneSdk
   cmake --build vendor/mds-tools/build -j"$(nproc)"
   ```
4. Build and certify (takes a while): `python3 tools/mds_build.py machines/drone --certify`
   → `build/drone/` (`.mds`, `.syx`, certifier report)
5. Optional, to listen without hardware: `tools/render/build.sh`, then
   `tools/render/mds-render build/drone/drone.mds out.wav -s 30 -t 1000 -p 3=127`
   (parameter index = value; `-c 5` lists the five most expensive blocks).

## How it works (short)

- The 24-bit phase accumulators are the saw waves themselves (no band-limiting – a little
  aliasing is part of the character). The sub is a clipped saw one octave down.
- Saturation uses the DSP's limiter plus a cubic curve (1.5u − 0.5u³); the ladder is four
  one-pole stages with resonance fed back from the last stage.
- The feedback delay lives in the track's external memory (1,536 words, up to 34 ms). It is
  copied into the output buffer once per block and read back sample by sample, which keeps
  DRAM page changes low on MKI. Memory not yet written since the machine was loaded is never read.
- Coefficients are recomputed in four groups, one group per block (drift + WARP + limiter, pitches,
  cutoff, levels + feedback), every 2.9 ms. On MKI the program fetches into an empty cache cost more
  than the arithmetic, so each block runs as little distinct code as possible.
- Drift and WARP share one routine: random targets with low probability, followed slowly.

## Credits

- [Claude Code](https://claude.com/claude-code) (Anthropic) for the DSP code, model and tooling
  of this port (see "How this was made").
- jmamma for the MDX firmware, the Machine Dump Standard and mds-tools.
- positionhigh, developer of [MicroDexed-touch](https://codeberg.org/positionhigh/MicroDexed-touch),
  the hardware my doergbox is built on – thanks and greetings!
- The dsp56300 emulator project (used inside mds-certifier).

## License

GPL-3.0 – see [LICENSE](LICENSE). The submodules keep their own licenses.

## Changelog

- **0.5** – first public release.
