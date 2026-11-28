#!/bin/sh
# Prepares a Ghidra install for Mac 68k code resources (XCMD/XFCN):
#   sh tools/ghidra/setup.sh GHIDRA_DIR MULTIVERSAL_DIR [DATA_DIR]
# GHIDRA_DIR is an unpacked Ghidra 11.x (https://github.com/NationalSecurityAgency/ghidra/releases);
# MULTIVERSAL_DIR is a clone of https://github.com/autc04/multiversal (MIT, Wolfgang Thaller);
# DATA_DIR (default ~/.cache/hcx-ghidra) receives traps.json and types.json.
# The 68k language files get the A-trap and Mac calling-convention patches
# (the originals are kept as *.orig) and the 68020 spec is recompiled.
set -e
G=${1:?Ghidra folder}; MV=${2:?Multiversal folder}; D=${3:-$HOME/.cache/hcx-ghidra}
HERE=$(cd "$(dirname "$0")" && pwd)
L=$G/Ghidra/Processors/68000/data/languages
for f in 68020.slaspec 68000.cspec; do
    [ -f "$L/$f.orig" ] || cp "$L/$f" "$L/$f.orig"
    cp "$L/$f.orig" "$L/$f"
done
patch -s "$L/68020.slaspec" "$HERE/68020-atrap.patch"
patch -s "$L/68000.cspec" "$HERE/68000-mac.patch"
(cd "$L" && "$G/support/sleigh" 68020.slaspec)
mkdir -p "$D"
python3 "$HERE/mktraps.py" "$MV" "$D/traps.json"
python3 "$HERE/mktypes.py" "$MV" "$D/types.json"
echo "ready: HCX_GHIDRA=$G HCX_GHIDRA_DATA=$D sh tools/ghidra/decompile.sh CODE.bin OUT.c"
