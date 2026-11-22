#!/bin/sh
# Builds the 68000 core of tools/xemu (a development tool, not part of HCX).
# Usage: cpu/build.sh [OUTPUT.so]   (default: tools/xemu/_hcxcpu.so)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(realpath -m "${1:-$HERE/../_hcxcpu.so}")
cd "$HERE"
T=$(mktemp -d)
cc -O2 -o "$T/m68kmake" musashi/m68kmake.c
cp musashi/m68k_in.c "$T/"
(cd "$T" && ./m68kmake . m68k_in.c >/dev/null)
cc -O2 -fPIC -shared -w -I musashi -I "$T" -o "$OUT" hcxcpu.c musashi/m68kcpu.c musashi/m68kdasm.c \
   "$T/m68kops.c" musashi/softfloat/softfloat.c -lm
rm -rf "$T"
