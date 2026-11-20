#!/bin/sh
# Builds HCX-x86_64.AppImage from this folder.
# Needs: mksquashfs (squashfs-tools) and the AppImage type2 runtime
# (https://github.com/AppImage/type2-runtime/releases, file "runtime-x86_64").
# Usage: ./build-appimage.sh [path/to/runtime-x86_64]
set -e
cd "$(dirname "$0")"
RUNTIME="${1:-runtime-x86_64}"
[ -f "$RUNTIME" ] || { echo "AppImage runtime not found: $RUNTIME" >&2; exit 1; }
rm -rf build/HCX.AppDir
A=build/HCX.AppDir
mkdir -p $A/usr/share/hcx $A/usr/share/applications $A/usr/share/metainfo $A/usr/share/icons/hicolor/scalable/apps
cp -r hcx $A/usr/share/hcx/
find $A -name __pycache__ -prune -exec rm -rf {} +
cp packaging/AppRun $A/AppRun && chmod +x $A/AppRun
cp packaging/io.github.pgostation.HCX.desktop $A/
cp packaging/io.github.pgostation.HCX.desktop $A/usr/share/applications/
cp packaging/io.github.pgostation.HCX.svg $A/
cp packaging/io.github.pgostation.HCX.svg $A/usr/share/icons/hicolor/scalable/apps/
cp packaging/*.xml $A/usr/share/metainfo/
rm -f build/hcx.squashfs HCX-x86_64.AppImage
mksquashfs $A build/hcx.squashfs -root-owned -noappend -comp zstd -quiet
cat "$RUNTIME" build/hcx.squashfs > HCX-x86_64.AppImage
chmod +x HCX-x86_64.AppImage
echo "OK: HCX-x86_64.AppImage"
