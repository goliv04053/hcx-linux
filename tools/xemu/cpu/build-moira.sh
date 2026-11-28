#!/bin/sh
# Builds the Moira variant of the tools/xemu core, a second CPU to cross-check
# Musashi (development tool, not part of HCX). Moira (MIT, Dirk W. Hoffmann)
# is not in this repository: clone https://github.com/dirkwhoffmann/Moira first.
# Usage: cpu/build-moira.sh MOIRA_FOLDER [OUTPUT.so]   (default: tools/xemu/_hcxmoira.so)
# Then: HCX_CPU_LIB=tools/xemu/_hcxmoira.so python3.12 -m tools.xemu.compare ...
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
M=$(realpath "${1:?Moira folder}")/Moira
OUT=$(realpath -m "${2:-$HERE/../_hcxmoira.so}")
c++ -std=c++20 -O2 -fPIC -shared -w -I "$M" -o "$OUT" "$HERE/hcxmoira.cpp" \
    "$M/Moira.cpp" "$M/MoiraCore68000.cpp" "$M/MoiraCore68010.cpp" "$M/MoiraCore68020.cpp" \
    "$M/MoiraDebugger.cpp" "$M/Cache/Cache.cpp" "$M/FPU/MFloat80.cpp" "$M/FPU/FpuEnv.cpp" \
    "$M/FPU/FpuEnvMath.cpp" "$M/FPU/FpuFormats.cpp" "$M/FPU/FPU.cpp" "$M/MMU/MMU030.cpp" "$M/MMU/MMU040.cpp"
