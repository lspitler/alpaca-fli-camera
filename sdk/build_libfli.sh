#!/usr/bin/env bash
#
# Build a *shared* libfli library from the vendored libfli-1.104 source.
#
# The stock Makefile only builds a static archive (libfli.a). The Alpaca
# server loads libfli through ctypes, which needs a shared object, so this
# script compiles the same object files and links them into a shared library:
#
#   Linux  -> libfli.so   (talks to the fliusb kernel module via /dev/fliusbN)
#   macOS  -> libfli.dylib (dev/signature testing only; no kernel driver)
#
# Usage:
#   ./build_libfli.sh            # builds into sdk/  (libfli.so / libfli.dylib)
#   LIBFLI_SRC=... OUT=... ./build_libfli.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${LIBFLI_SRC:-$HERE/libfli-1.104}"
OUT_DIR="${OUT:-$HERE}"

UNAME="$(uname -s)"
CC="${CC:-gcc}"
CFLAGS="-Wall -O2 -g -fPIC -I$SRC -I$SRC/unix"

# Platform-specific source dirs + link flags mirror the stock Makefile's
# VPATH selection (unix/linux, unix/osx, ...).
case "$UNAME" in
  Linux)
    PLAT_SRC="$SRC/unix/linux"
    OUT_LIB="$OUT_DIR/libfli.so"
    LDFLAGS="-shared -Wl,-soname,libfli.so"
    LIBS=""
    # Linux uses the fliusb kernel char device, not libusb.
    ;;
  Darwin)
    PLAT_SRC="$SRC/unix/osx"
    OUT_LIB="$OUT_DIR/libfli.dylib"
    LDFLAGS="-dynamiclib -install_name @rpath/libfli.dylib"
    LIBS="-framework IOKit -framework CoreFoundation"
    CFLAGS="$CFLAGS -I$SRC/unix/osx"
    ;;
  *)
    echo "Unsupported platform: $UNAME" >&2
    exit 1
    ;;
esac

# Object set matches the Makefile's ALLOBJ (minus parport on non-Linux).
SOURCES=(
  "$SRC/libfli.c"
  "$SRC/libfli-mem.c"
  "$SRC/libfli-camera.c"
  "$SRC/libfli-camera-parport.c"
  "$SRC/libfli-camera-usb.c"
  "$SRC/libfli-filter-focuser.c"
  "$SRC/unix/libfli-usb.c"
  "$SRC/unix/libfli-serial.c"
  "$SRC/unix/libfli-sys.c"
  "$SRC/unix/libfli-debug.c"
  "$PLAT_SRC/libfli-usb-sys.c"
)
if [ "$UNAME" = "Linux" ]; then
  SOURCES+=("$SRC/unix/linux/libfli-parport.c")
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Building shared libfli for $UNAME -> $OUT_LIB"
OBJS=()
for s in "${SOURCES[@]}"; do
  if [ ! -f "$s" ]; then
    echo "  skip (missing): $s" >&2
    continue
  fi
  o="$TMP/$(basename "${s%.c}").o"
  echo "  CC $(basename "$s")"
  # shellcheck disable=SC2086
  "$CC" $CFLAGS -c "$s" -o "$o"
  OBJS+=("$o")
done

echo "  LD $(basename "$OUT_LIB")"
# shellcheck disable=SC2086
"$CC" $LDFLAGS -o "$OUT_LIB" "${OBJS[@]}" $LIBS

echo "Done: $OUT_LIB"
