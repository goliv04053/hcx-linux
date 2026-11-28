#!/bin/sh
# Decompiles one 68k code resource with the Mac traps named and typed:
#   HCX_GHIDRA=GHIDRA_DIR HCX_GHIDRA_DATA=DATA_DIR sh tools/ghidra/decompile.sh CODE.bin OUT.c
# Writes OUT.c (C for every function), OUT.asm (listing with call targets
# and dispatcher selectors) and OUT.log (Ghidra's log).
# CODE.bin comes from: python3 -m tools.xemu.compare FILE STACK --dump NAME CODE.bin
set -e
: "${HCX_GHIDRA:?set HCX_GHIDRA to the Ghidra folder}"
export HCX_GHIDRA_DATA=${HCX_GHIDRA_DATA:-$HOME/.cache/hcx-ghidra}
HERE=$(cd "$(dirname "$0")" && pwd)
P=$(mktemp -d)
"$HCX_GHIDRA/support/analyzeHeadless" "$P" p -import "$1" -overwrite -loader BinaryLoader \
    -processor 68000:BE:32:MC68020 -scriptPath "$HERE" -preScript MacTraps.java \
    -postScript DecompileAll.java "$2" -deleteProject > "${2%.c}.log" 2>&1
rmdir "$P" 2>/dev/null || true
