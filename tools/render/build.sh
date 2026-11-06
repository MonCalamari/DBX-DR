#!/usr/bin/env bash
# Baut tools/render/mds-render gegen den bereits gebauten mds-certifier (vendor/mds-tools/build).
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="$ROOT/vendor/mds-tools"
B="$T/build/certifier"
c++ -std=gnu++17 -O2 -DNDEBUG -DDSP56300_DEBUGGER=1 -DASMJIT_STATIC \
  -I"$T/certifier/src" -I"$B/dsp56300/source" -I"$T/external/dsp56300/source" -I"$T/external/dsp56300/source/asmjit/src" \
  "$ROOT/tools/render/mds_render.cpp" "$T/certifier/src/emulator.cpp" "$T/certifier/src/package.cpp" \
  "$T/certifier/src/services.cpp" \
  "$B/dsp56kEmu/libdsp56kEmu.a" "$B/vtuneSdk/libvtuneSdk.a" -ldl "$B/asmjit/libasmjit.a" -lpthread -lrt \
  -o "$ROOT/tools/render/mds-render"
echo "gebaut: $ROOT/tools/render/mds-render"
