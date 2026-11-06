// mds-render: führt eine .mds-Machine im DSP56303-Emulator des mds-certifiers aus
// und schreibt die Ausgabe einer Spur als 24-Bit-WAV (44,1 kHz, mono).
//
//   mds-render machine.mds out.wav [-s SEKUNDEN] [-t SEKUNDEN_ZWISCHEN_TRIGS] [-d BLÖCKE] [-f SEKUNDEN_BIS_ZUM_ERSTEN_TRIG]
//              [-p INDEX=WERT ...] [-c N]   (-c: die N teuersten Blöcke mit MKII/MKI-Zyklen ausgeben)
//
// Standard: 4,5 s, Trig alle 0,5 s (Viertel bei 120 BPM), Regler auf den Defaults.
// Gibt am Ende die höchsten gemessenen Zyklen pro Block (MKII/MKI) aus – nur als
// Anhaltspunkt; maßgeblich ist die Zertifizierung.
#include "emulator.hpp"
#include "layout.hpp"
#include "package.hpp"
#include "services.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

using namespace mds;
using namespace dsp56k;

static void put16(std::ofstream& f, uint16_t v) { f.put(char(v & 0xff)); f.put(char(v >> 8)); }
static void put32(std::ofstream& f, uint32_t v) { put16(f, uint16_t(v & 0xffff)); put16(f, uint16_t(v >> 16)); }

static bool write_wav(const char* path, const std::vector<uint32_t>& words) {
    std::ofstream f(path, std::ios::binary);
    if (!f) return false;
    const uint32_t bytes = uint32_t(words.size() * 3);
    f.write("RIFF", 4); put32(f, 36 + bytes); f.write("WAVEfmt ", 8);
    put32(f, 16); put16(f, 1); put16(f, 1); put32(f, 44100); put32(f, 44100 * 3); put16(f, 3); put16(f, 24);
    f.write("data", 4); put32(f, bytes);
    for (uint32_t w : words) { f.put(char(w & 0xff)); f.put(char((w >> 8) & 0xff)); f.put(char((w >> 16) & 0xff)); }
    return bool(f);
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "Aufruf: %s machine.mds out.wav [-s sek] [-t sek] [-p index=wert ...]\n", argv[0]);
        return 2;
    }
    double seconds = 4.5, trig_every = 0.5;
    uint32_t dump_blocks = 0, top_blocks = 0;
    double first = 0.0;         // -f SEKUNDEN: erster Trig (Vorlauf)   // -d N: Y-Arbeitsspeicher nach den ersten N Blöcken ausgeben
    std::vector<std::pair<int, int>> sets;
    for (int i = 3; i < argc; ++i) {
        if (!std::strcmp(argv[i], "-s") && i + 1 < argc) seconds = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "-t") && i + 1 < argc) trig_every = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "-f") && i + 1 < argc) first = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "-d") && i + 1 < argc) dump_blocks = uint32_t(std::atoi(argv[++i]));
        else if (!std::strcmp(argv[i], "-c") && i + 1 < argc) top_blocks = uint32_t(std::atoi(argv[++i]));
        else if (!std::strcmp(argv[i], "-p") && i + 1 < argc) {
            int idx = 0, val = 0;
            if (std::sscanf(argv[++i], "%d=%d", &idx, &val) != 2) { std::fprintf(stderr, "-p erwartet INDEX=WERT\n"); return 2; }
            sets.emplace_back(idx, val);
        } else { std::fprintf(stderr, "Unbekannte Option %s\n", argv[i]); return 2; }
    }

    std::ifstream in(argv[1], std::ios::binary);
    std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    Package package;
    std::string error;
    if (!parse_package(bytes, package, error)) { std::fprintf(stderr, "Paket ungültig: %s\n", error.c_str()); return 1; }

    std::vector<uint8_t> settings(package.parameters.size());
    for (size_t i = 0; i != settings.size(); ++i) settings[i] = package.parameters[i].default_value;
    for (auto [idx, val] : sets) {
        if (idx < 0 || size_t(idx) >= settings.size()) { std::fprintf(stderr, "Regler %d gibt es nicht\n", idx); return 2; }
        settings[size_t(idx)] = uint8_t(val);
    }

    Emulator emu;
    Services services(Hardware::Mk2UW);
    services.reset(1, false);
    for (uint32_t i = 0; i != kSineWords; ++i)
        emu.set(MemArea_X, kSineTable + i,
                uint32_t(std::lround(std::sin(2.0 * M_PI * i / kSineWords) * 8388607.0)) & kMask24);
    emu.set(MemArea_X, kInputCursor, kInputRing);
    emu.install(package, kProgramRegion, services);

    const uint32_t track = 0, ws = workset(track), out = kOutputBuffer[0];
    for (uint32_t w = 0; w != kWorksetWords; ++w) { emu.set(MemArea_X, ws + w, 0); emu.set(MemArea_Y, ws + w, 0); }
    for (size_t i = 0; i != settings.size(); ++i)
        emu.set(MemArea_Y, ws + 1 + uint32_t(i), parameter_value(package.parameters[i], settings[i]));

    const uint32_t blocks = uint32_t(seconds * 44100.0 / kBlock);
    const uint32_t trig_blocks = trig_every > 0 ? std::max<uint32_t>(1, uint32_t(trig_every * 44100.0 / kBlock)) : 0;
    std::vector<uint32_t> audio;
    uint64_t worst[kModels] = {};
    std::vector<std::array<uint64_t, 3>> per_block;   // MKI, MKII, Block

    auto call = [&](Routine routine, uint32_t entry, Costs& costs) -> bool {
        Permissions perm;
        perm.track = track;
        perm.routine = routine;
        perm.x_words = package.x_words;
        perm.y_words = package.y_words;
        perm.temp_x = package.imports_symbol(kSymTempX);
        perm.temp_y = package.imports_symbol(kSymTempY);
        perm.track_memory = package.imports_symbol(kSymTrackMemory);
        perm.trigger_info = package.imports_symbol(kSymTriggerInfo);
        for (EReg r : {Reg_M0, Reg_M1, Reg_M2, Reg_M3, Reg_M4, Reg_M5, Reg_M6, Reg_M7}) emu.set_reg(r, kMask24);
        emu.set_reg(Reg_R6, ws);
        if (routine == Routine::Init) emu.set_reg(Reg_R1, track);
        if (routine == Routine::Render) {
            perm.output = out;
            emu.set_reg(Reg_R7, out);
            emu.set_reg(Reg_M7, kBlock - 1);
        }
        std::string err;
        if (!emu.call(kProgramRegion + entry, perm, costs, err)) {
            std::fprintf(stderr, "Fehler: %s\n", err.c_str());
            return false;
        }
        return true;
    };

    for (uint32_t b = 0; b != blocks; ++b) {
        emu.begin_track();
        Costs costs;
        if (b == 0 && !call(Routine::Init, package.init, costs)) return 1;
        const uint32_t first_block = uint32_t(first * 44100.0 / kBlock);
        if (trig_blocks && b >= first_block && (b - first_block) % trig_blocks == 0 &&
            !call(Routine::Trig, package.trig, costs)) return 1;
        if (!call(Routine::Render, package.render, costs)) return 1;
        for (unsigned m = 0; m != kModels; ++m) worst[m] = std::max(worst[m], costs.cycles[m]);
        per_block.push_back({costs.cycles[kMk1], costs.cycles[kMk2], b});
        for (uint32_t w = 0; w != kBlock; ++w) audio.push_back(emu.get(MemArea_Y, out + w));
        if (dump_blocks && b < dump_blocks) {
            std::printf("Block %u Y:", b);
            for (uint32_t w = 0; w != package.y_words; ++w) std::printf(" %u=%06X", w, emu.get(MemArea_Y, ws + w));
            std::printf("\nBlock %u X:", b);
            for (uint32_t w = 0; w != package.x_words; ++w) std::printf(" %u=%06X", w, emu.get(MemArea_X, ws + w));
            std::printf("\n");
        }
    }

    if (top_blocks) {
        std::sort(per_block.begin(), per_block.end(), [](auto& x, auto& y) { return x[0] > y[0]; });
        for (uint32_t i = 0; i != top_blocks && i != per_block.size(); ++i)
            std::printf("Block %llu: MKII %llu, MKI %llu\n", (unsigned long long)per_block[i][2],
                        (unsigned long long)per_block[i][1], (unsigned long long)per_block[i][0]);
    }
    double peak = 0;
    for (uint32_t w : audio) {
        int32_t s = int32_t(w << 8) >> 8;
        peak = std::max(peak, std::fabs(s / 8388608.0));
    }
    if (!write_wav(argv[2], audio)) { std::fprintf(stderr, "Kann %s nicht schreiben\n", argv[2]); return 1; }
    std::printf("%s: %u Blöcke, Spitze %.1f dBFS, höchstens %llu Zyklen/Block MKII, %llu MKI (Anhaltspunkt)\n",
                argv[2], blocks, peak > 0 ? 20 * std::log10(peak) : -999.0,
                (unsigned long long)worst[kMk2], (unsigned long long)worst[kMk1]);
    return 0;
}
